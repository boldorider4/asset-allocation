# SPDX-License-Identifier: AGPL-3.0-or-later
"""ISIN metadata and post-add price lookup for the draft flow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.store_helpers import db_path, write_cache

from visual.web.backend.isin_lookup import fetch_price_for_isin, lookup_isin

_ISIN = "IE00BHZPJ239"


class TestLookupIsin(unittest.TestCase):
    def test_returns_name_and_cached_price_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            write_cache(db_file, {_ISIN: {"price": 12.5}})
            with patch(
                "position.justetf_position.just_etf_name",
                return_value="Example ETF",
            ), patch(
                "position.justetf_position.just_etf_product_url_exists"
            ) as probe:
                result = lookup_isin(db_file, f" {_ISIN.lower()} ")
        self.assertTrue(result.exists)
        self.assertEqual(result.name, "Example ETF")
        self.assertEqual(result.price, 12.5)
        probe.assert_not_called()

    def test_cached_price_keeps_isin_known_when_name_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            write_cache(db_file, {_ISIN: {"price": 12.5}})
            with patch(
                "position.justetf_position.just_etf_name",
                return_value=None,
            ), patch(
                "position.justetf_position.just_etf_product_url_exists"
            ) as probe:
                result = lookup_isin(db_file, _ISIN)
        self.assertTrue(result.exists)
        self.assertIsNone(result.name)
        self.assertEqual(result.price, 12.5)
        probe.assert_not_called()

    def test_name_alone_is_enough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            with patch(
                "position.justetf_position.just_etf_name",
                return_value="Example ETF",
            ), patch(
                "position.justetf_position.just_etf_product_url_exists"
            ) as probe:
                result = lookup_isin(db_file, _ISIN)
        self.assertTrue(result.exists)
        self.assertEqual(result.name, "Example ETF")
        self.assertIsNone(result.price)
        probe.assert_not_called()

    def test_falls_back_to_chart_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            with patch(
                "position.justetf_position.just_etf_name",
                return_value=None,
            ), patch(
                "position.justetf_position.just_etf_product_url_exists",
                return_value=True,
            ) as probe:
                result = lookup_isin(db_file, _ISIN)
        self.assertTrue(result.exists)
        probe.assert_called_once_with(_ISIN)

    def test_missing_product_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            with patch(
                "position.justetf_position.just_etf_name",
                return_value=None,
            ), patch(
                "position.justetf_position.just_etf_product_url_exists",
                return_value=False,
            ):
                result = lookup_isin(db_file, "XX0000000000")
        self.assertFalse(result.exists)
        self.assertIsNone(result.name)
        self.assertIsNone(result.price)

    def test_empty_isin_skips_all_work(self) -> None:
        with patch("position.justetf_position.just_etf_name") as name:
            result = lookup_isin("", "   ")
        self.assertFalse(result.exists)
        name.assert_not_called()


class TestFetchPriceForIsin(unittest.TestCase):
    def test_factory_runs_with_price_only_flags(self) -> None:
        seen = {}

        def fake_factory(isin, *, ctx, write_cache):
            seen["isin"] = isin
            seen["ctx"] = ctx
            seen["write_cache"] = write_cache
            return SimpleNamespace(price=42.0)

        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            with patch("position.factory.factory", side_effect=fake_factory):
                price = fetch_price_for_isin(db_file, f" {_ISIN.lower()} ", "alice")
        self.assertEqual(price, 42.0)
        self.assertEqual(seen["isin"], _ISIN)
        self.assertTrue(seen["write_cache"])
        config = seen["ctx"].config
        self.assertTrue(config.fetch_prices)
        self.assertFalse(config.fetch_geosplit)
        self.assertFalse(config.fetch_sectorsplit)
        self.assertEqual(config.position_source, "justetf")
        self.assertEqual(config.user, "alice")
        self.assertEqual(Path(config.server.db_file), Path(db_file))

    def test_factory_failure_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = db_path(tmp)
            with patch(
                "position.factory.factory", side_effect=RuntimeError("blocked")
            ):
                self.assertIsNone(fetch_price_for_isin(db_file, _ISIN, "alice"))

    def test_empty_inputs_skip_factory(self) -> None:
        with patch("position.factory.factory") as factory:
            self.assertIsNone(fetch_price_for_isin("", _ISIN, "alice"))
            self.assertIsNone(fetch_price_for_isin("db.sqlite", "   ", "alice"))
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
