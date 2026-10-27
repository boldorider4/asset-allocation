# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vanguard GraphQL country/sector aggregation and factory routing."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import position.vanguard_position as vanguard_mod
from cli.context import AppConfig, RuntimeContext, ServerConfig
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.vanguard_position import (
    VanguardPosition,
    vanguard_product_ref_for_isin,
    vanguard_product_url_exists,
)

_ISIN = "IE00BK5BQT80"
_PORT_ID = "9679"
_PROFILE_FTSE = {
    "fundFullName": "Vanguard FTSE All-World UCITS ETF (USD) Accumulating",
    "assetClassificationLevel1": "Equity",
    "marketOfDomicile": "IRELAND",
    "primaryMarketEquityClassification": "FTSE Country of Risk",
    "primarySectorEquityClassification": "ICB Sectors",
}


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _json_response(payload: object, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": "application/json"}
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _market_row(
    name: str | None,
    weight: float | None,
    holding_stat_code: str,
    country_code: str | None = None,
) -> dict[str, object]:
    return {
        "countryCode": country_code,
        "countryName": name,
        "fundMktPercent": weight,
        "holdingStatCode": holding_stat_code,
    }


def _sector_row(name: str | None, weight: float | None) -> dict[str, object]:
    return {
        "sectorCode": "X",
        "date": "2026-09-30",
        "sectorName": name,
        "fundPercent": weight,
        "benchmarkPercent": None,
    }


def _fund(
    *,
    profile: dict[str, object] | None = None,
    markets: list[dict[str, object]] | None = None,
    sectors: list[dict[str, object]] | None = None,
    port_id: str = _PORT_ID,
) -> dict[str, object]:
    return {
        "portId": port_id,
        "profile": _PROFILE_FTSE if profile is None else profile,
        "marketAllocation": markets or [],
        "sectorDiversification": sectors or [],
    }


def _fund_response(fund: dict[str, object]) -> dict[str, object]:
    return {"data": {"funds": [fund]}}


def _ref_response(ref: str | None) -> dict[str, object]:
    funds = [] if ref is None else [{"portId": ref}]
    return {"data": {"funds": funds}}


def _exists_response(
    ref: str, isin: str, *, returned_ref: str | None = None
) -> dict[str, object]:
    return {
        "data": {
            "funds": [
                {
                    "portId": ref if returned_ref is None else returned_ref,
                    "profile": {
                        "identifiers": [
                            {
                                "altId": "ISIN",
                                "altIdValue": isin,
                                "altIdCode": "ISIN",
                            }
                        ]
                    },
                }
            ]
        }
    }


def _ctx(*, fetch_geosplit: bool = True, fetch_sectorsplit: bool = False) -> RuntimeContext:
    holder = tempfile.TemporaryDirectory()
    tmp = Path(holder.name)
    ctx = RuntimeContext(
        config=AppConfig(
            fetch_geosplit=fetch_geosplit,
            fetch_sectorsplit=fetch_sectorsplit,
            fetch_prices=False,
            server=ServerConfig(
                port=0,
                address="localhost",
                directory=tmp,
                db_file=tmp / "asalloc.db",
            ),
        )
    )
    ctx.cache = {}
    ctx.cache_loaded = True
    ctx._holder = holder  # type: ignore[attr-defined]
    return ctx


class TestVanguardProductRef(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_refs = dict(vanguard_mod._VANGUARD_PRODUCT_REFS)
        vanguard_mod._VANGUARD_PRODUCT_REFS.clear()

        def restore() -> None:
            vanguard_mod._VANGUARD_PRODUCT_REFS.clear()
            vanguard_mod._VANGUARD_PRODUCT_REFS.update(self._orig_refs)

        self.addCleanup(restore)

    def test_maps_isin_to_port_id(self) -> None:
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_ref_response(_PORT_ID))
        ) as opener:
            self.assertEqual(vanguard_product_ref_for_isin(_ISIN), _PORT_ID)
        opener.assert_called_once()

    def test_unknown_isin_returns_none(self) -> None:
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_ref_response(None))
        ):
            self.assertIsNone(vanguard_product_ref_for_isin(_ISIN))

    def test_lookup_failure_returns_none(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertIsNone(vanguard_product_ref_for_isin(_ISIN))

    def test_graphql_errors_return_none(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response({"errors": [{"message": "boom"}]}),
        ):
            self.assertIsNone(vanguard_product_ref_for_isin(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertIsNone(vanguard_product_ref_for_isin(""))
        opener.assert_not_called()

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_ref_response(_PORT_ID))
        ) as opener:
            self.assertEqual(vanguard_product_ref_for_isin(_ISIN), _PORT_ID)
            self.assertEqual(vanguard_product_ref_for_isin(_ISIN), _PORT_ID)
        opener.assert_called_once()


class TestVanguardProductExists(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_refs = dict(vanguard_mod._VANGUARD_PRODUCT_REFS)
        self._orig_exists = dict(vanguard_mod._VANGUARD_PRODUCT_EXISTS)
        vanguard_mod._VANGUARD_PRODUCT_REFS.clear()
        vanguard_mod._VANGUARD_PRODUCT_EXISTS.clear()

        def restore() -> None:
            vanguard_mod._VANGUARD_PRODUCT_REFS.clear()
            vanguard_mod._VANGUARD_PRODUCT_REFS.update(self._orig_refs)
            vanguard_mod._VANGUARD_PRODUCT_EXISTS.clear()
            vanguard_mod._VANGUARD_PRODUCT_EXISTS.update(self._orig_exists)

        self.addCleanup(restore)

    def test_exists_on_matching_isin(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(_exists_response(_PORT_ID, _ISIN)),
        ):
            self.assertTrue(vanguard_product_url_exists(_ISIN, _PORT_ID))

    def test_missing_product_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_ref_response(None))
        ):
            self.assertFalse(vanguard_product_url_exists(_ISIN, _PORT_ID))

    def test_mismatched_isin_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(_exists_response(_PORT_ID, "IE00OTHER000")),
        ):
            self.assertFalse(vanguard_product_url_exists(_ISIN, _PORT_ID))

    def test_mismatched_port_id_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(
                _exists_response(_PORT_ID, _ISIN, returned_ref="9999")
            ),
        ):
            self.assertFalse(vanguard_product_url_exists(_ISIN, _PORT_ID))

    def test_without_ref_uses_isin_lookup(self) -> None:
        responses = [
            _json_response(_ref_response(_PORT_ID)),
            _json_response(_exists_response(_PORT_ID, _ISIN)),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as opener:
            self.assertTrue(vanguard_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 2)

    def test_network_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertFalse(vanguard_product_url_exists(_ISIN, _PORT_ID))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(vanguard_product_url_exists("", _PORT_ID))
        opener.assert_not_called()

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(_exists_response(_PORT_ID, _ISIN)),
        ) as opener:
            self.assertTrue(vanguard_product_url_exists(_ISIN, _PORT_ID))
            self.assertTrue(vanguard_product_url_exists(_ISIN, _PORT_ID))
        opener.assert_called_once()


class TestVanguardAggregation(unittest.TestCase):
    def test_ftse_country_selection_and_alias(self) -> None:
        fund = _fund(
            markets=[
                _market_row("United States", 60.0, "FTCTYATPCS"),
                _market_row("Korea", 5.0, "FTCTYATPCS"),
                _market_row("Japan", 34.5, "FTCTYATPCS"),
                _market_row("Other", 0.5, "FTCTYATPCS"),
                _market_row("Russia", 0.0, "FTCTYATPCS"),
                _market_row(None, 1.0, "FTCTYATPCS"),
                _market_row("United States", 99.0, "MSCTYATPCS"),
            ]
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [
                {"name": "United States", "weight_pct": 60.0},
                {"name": "Japan", "weight_pct": 34.5},
                {"name": "South Korea", "weight_pct": 5.0},
                {"name": "Other", "weight_pct": 0.5},
            ],
        )

    def test_msci_country_selection(self) -> None:
        profile = dict(
            _PROFILE_FTSE,
            primaryMarketEquityClassification="MSCI Country of Risk",
        )
        fund = _fund(
            profile=profile,
            markets=[
                _market_row("United States", 100.0, "MSCTYATPCS"),
                _market_row("France", 50.0, "FTCTYATPCS"),
            ],
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [{"name": "United States", "weight_pct": 100.0}],
        )

    def test_bond_uses_cntryemug(self) -> None:
        profile = dict(
            _PROFILE_FTSE,
            assetClassificationLevel1="Bond",
            primaryMarketEquityClassification=None,
        )
        fund = _fund(
            profile=profile,
            markets=[
                _market_row("United States", 80.0, "CNTRYEMUG"),
                _market_row("Germany", 20.0, "CNTRYEMUG"),
                _market_row("France", 100.0, "CNTRYATPCB"),
            ],
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [
                {"name": "United States", "weight_pct": 80.0},
                {"name": "Germany", "weight_pct": 20.0},
            ],
        )

    def test_us_without_primary_uses_cntryatpcb(self) -> None:
        profile = dict(
            _PROFILE_FTSE,
            marketOfDomicile="UNITED STATES",
            primaryMarketEquityClassification=None,
        )
        fund = _fund(
            profile=profile,
            markets=[
                _market_row("United States", 100.0, "CNTRYATPCB"),
                _market_row("France", 100.0, "CNTRYEMUG"),
            ],
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [{"name": "United States", "weight_pct": 100.0}],
        )

    def test_money_market_uses_cntryatpcb(self) -> None:
        profile = dict(
            _PROFILE_FTSE,
            assetClassificationLevel1="Money Market",
            primaryMarketEquityClassification=None,
        )
        fund = _fund(
            profile=profile,
            markets=[
                _market_row("United States", 100.0, "CNTRYATPCB"),
                _market_row("France", 100.0, "CNTRYEMUG"),
            ],
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [{"name": "United States", "weight_pct": 100.0}],
        )

    def test_fallback_uses_cntryemug(self) -> None:
        profile = dict(
            _PROFILE_FTSE,
            primaryMarketEquityClassification=None,
        )
        fund = _fund(
            profile=profile,
            markets=[
                _market_row("France", 100.0, "CNTRYEMUG"),
                _market_row("United States", 100.0, "CNTRYATPCB"),
            ],
        )
        self.assertEqual(
            VanguardPosition._countries_from_fund(fund),
            [{"name": "France", "weight_pct": 100.0}],
        )

    def test_sector_canonicalization(self) -> None:
        fund = _fund(
            sectors=[
                _sector_row("Consumer Discretionary", 10.0),
                _sector_row("Consumer Staples", 4.0),
                _sector_row("Basic Materials", 3.0),
                _sector_row("Energy", 5.0),
                _sector_row("Utilities", 2.0),
                _sector_row("Real Estate", 1.0),
                _sector_row("Health Care", 8.0),
                _sector_row("Technology", 36.0),
                _sector_row("Financials", 15.0),
                _sector_row("Telecommunications", 3.0),
                _sector_row("Industrials", 12.0),
                _sector_row("Other", 1.0),
                _sector_row("Zero", 0.0),
                _sector_row(None, 10.0),
            ]
        )
        rows = VanguardPosition._sectors_from_fund(fund)
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 36.0},
                {"name": "Finance", "weight_pct": 15.0},
                {"name": "Consumer", "weight_pct": 14.0},
                {"name": "Industrials", "weight_pct": 12.0},
                {"name": "Healthcare", "weight_pct": 8.0},
                {"name": "Commodities", "weight_pct": 5.0},
                {"name": "Materials", "weight_pct": 3.0},
                {"name": "Telecommunication", "weight_pct": 3.0},
                {"name": "Utilities", "weight_pct": 2.0},
                {"name": "Real Estate", "weight_pct": 1.0},
                {"name": "Other", "weight_pct": 1.0},
            ],
        )

    def test_empty_sectors(self) -> None:
        self.assertEqual(VanguardPosition._sectors_from_fund(_fund()), [])


class TestVanguardPositionFetch(unittest.TestCase):
    def test_fetches_countries_and_sectors_from_one_payload(self) -> None:
        ctx = _ctx(fetch_geosplit=True, fetch_sectorsplit=True)
        ctx.isin_registry.register_isin(
            _ISIN,
            issuer="vanguard",
            bucket="equity_portfolio",
            product_ref=_PORT_ID,
        )
        fund = _fund(
            markets=[
                _market_row("United States", 60.0, "FTCTYATPCS"),
                _market_row("Korea", 5.0, "FTCTYATPCS"),
                _market_row("Japan", 35.0, "FTCTYATPCS"),
            ],
            sectors=[
                _sector_row("Technology", 60.0),
                _sector_row("Financials", 40.0),
            ],
        )
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_fund_response(fund))
        ) as opener:
            with patch.object(VanguardPosition, "_fast_info_price", return_value=12.0):
                pos = VanguardPosition(
                    _ISIN, name="Vanguard", shares=1, ctx=ctx
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "United States", "weight_pct": 60.0},
                {"name": "Japan", "weight_pct": 35.0},
                {"name": "South Korea", "weight_pct": 5.0},
            ],
        )
        self.assertEqual(
            pos.sectors(),
            [
                {"name": "Technology", "weight_pct": 60.0},
                {"name": "Finance", "weight_pct": 40.0},
            ],
        )
        opener.assert_called_once()

    def test_empty_sectors_fall_back_to_justetf(self) -> None:
        ctx = _ctx(fetch_geosplit=False, fetch_sectorsplit=True)
        ctx.isin_registry.register_isin(
            _ISIN,
            issuer="vanguard",
            bucket="fixed_income_portfolio",
            product_ref=_PORT_ID,
        )
        fund = _fund(
            profile=dict(
                _PROFILE_FTSE,
                assetClassificationLevel1="Bond",
                primaryMarketEquityClassification=None,
            )
        )
        fallback = [{"name": "Government", "weight_pct": 100.0}]
        with patch(
            "urllib.request.urlopen", return_value=_json_response(_fund_response(fund))
        ):
            with patch.object(
                JustETFPosition,
                "_http_sector_dist_json",
                return_value=fallback,
            ):
                with patch.object(
                    VanguardPosition, "_fast_info_price", return_value=12.0
                ):
                    pos = VanguardPosition(
                        _ISIN,
                        name="Vanguard",
                        shares=1,
                        cached_countries={},
                        ctx=ctx,
                    )
        self.assertEqual(pos.sectors(), fallback)


class TestVanguardFactoryRouting(unittest.TestCase):
    def _factory(self, ctx: RuntimeContext, **kwargs):
        defaults = {
            "isin": _ISIN,
            "name": "Vanguard FTSE All-World",
            "shares": 1,
            "price": 10.0,
        }
        defaults.update(kwargs)
        defaults["ctx"] = ctx
        return factory(**defaults)

    def _no_country_scrape(self):
        return patch.object(
            JustETFPosition, "_fetch_countries_with_retries", return_value=[]
        )

    def test_registry_hit_uses_vanguard_without_probes(self) -> None:
        ctx = _ctx()
        ctx.isin_registry.register_isin(
            _ISIN,
            issuer="vanguard",
            bucket="equity_portfolio",
            product_ref=_PORT_ID,
        )
        with (
            patch("position.factory.vanguard_product_ref_for_isin") as ref,
            patch("position.factory.vanguard_product_url_exists") as exists,
            patch.object(VanguardPosition, "_fast_info_price", return_value=12.0),
            self._no_country_scrape(),
        ):
            pos = self._factory(ctx)
        self.assertIsInstance(pos, VanguardPosition)
        ref.assert_not_called()
        exists.assert_not_called()

    def test_probe_hit_registers_vanguard_and_port_id(self) -> None:
        ctx = _ctx()
        with (
            patch("position.factory.dws_product_url_exists", return_value=False),
            patch("position.factory.ishares_product_ref_for_isin", return_value=None),
            patch("position.factory.amundi_product_url_exists", return_value=False),
            patch("position.factory.ssga_product_ref_for_isin", return_value=None),
            patch("position.factory.ubs_product_url_exists", return_value=False),
            patch("position.factory.invesco_product_url_exists", return_value=False),
            patch("position.factory.landg_product_url_exists", return_value=False),
            patch(
                "position.factory.vanguard_product_ref_for_isin",
                return_value=_PORT_ID,
            ),
            patch(
                "position.factory.vanguard_product_url_exists",
                return_value=True,
            ),
            patch.object(VanguardPosition, "_fast_info_price", return_value=12.0),
            self._no_country_scrape(),
        ):
            pos = self._factory(ctx)
        self.assertIsInstance(pos, VanguardPosition)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin(_ISIN), "vanguard")
        self.assertEqual(
            ctx.isin_registry.get_product_ref_for_isin(_ISIN), _PORT_ID
        )

    def test_probe_miss_falls_back_to_justetf(self) -> None:
        ctx = _ctx()
        with (
            patch("position.factory.dws_product_url_exists", return_value=False),
            patch("position.factory.ishares_product_ref_for_isin", return_value=None),
            patch("position.factory.amundi_product_url_exists", return_value=False),
            patch("position.factory.ssga_product_ref_for_isin", return_value=None),
            patch("position.factory.ubs_product_url_exists", return_value=False),
            patch("position.factory.invesco_product_url_exists", return_value=False),
            patch("position.factory.landg_product_url_exists", return_value=False),
            patch(
                "position.factory.vanguard_product_ref_for_isin",
                return_value=None,
            ),
            self._no_country_scrape(),
        ):
            pos = self._factory(ctx, isin="XX000VANMISS")
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, VanguardPosition)
        self.assertEqual(
            ctx.isin_registry.get_issuer_for_isin("XX000VANMISS"), "justetf"
        )


if __name__ == "__main__":
    unittest.main()
