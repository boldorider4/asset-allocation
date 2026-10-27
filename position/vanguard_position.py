# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from cli.logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_VANGUARD_GRAPHQL_URL = "https://global.vanguard.com/gpx/graphql"
_VANGUARD_CONSUMER_ID = "de7"
_VANGUARD_TIMEOUT_S = 30
_VANGUARD_HEADERS = {
    **JustETFPosition._HEADERS,
    "Content-Type": "application/json",
    "Origin": "https://global.vanguard.com",
    "Referer": "https://global.vanguard.com/",
    "X-Consumer-ID": _VANGUARD_CONSUMER_ID,
}

_VANGUARD_ISIN_QUERY = """
query VanguardFundByIsin($isins: [String!]!) {
  funds(isins: $isins) {
    portId
  }
}
"""

_VANGUARD_EXISTS_QUERY = """
query VanguardFundByPortId($portIds: [String!]!) {
  funds(portIds: $portIds) {
    portId
    profile {
      identifiers(altIds: ["ISIN"]) {
        altId
        altIdValue
        altIdCode
      }
    }
  }
}
"""

_VANGUARD_FUND_QUERY = """
query VanguardFundAllocation($portIds: [String!]!) {
  funds(portIds: $portIds) {
    portId
    profile {
      fundFullName
      assetClassificationLevel1
      marketOfDomicile
      primaryMarketEquityClassification
      primarySectorEquityClassification
    }
    marketAllocation {
      portId
      date
      countryCode
      countryName
      fundMktPercent
      holdingStatCode
      regionCode
      regionName
    }
    sectorDiversification {
      sectorCode
      date
      sectorName
      fundPercent
      benchmarkPercent
    }
  }
}
"""

_VANGUARD_PRODUCT_REFS: dict[str, str | None] = {}
_VANGUARD_PRODUCT_EXISTS: dict[tuple[str, str | None], bool] = {}


def _graphql_post(query: str, variables: dict[str, object]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        _VANGUARD_GRAPHQL_URL,
        data=body,
        headers=_VANGUARD_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_VANGUARD_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200)
            if not 200 <= status < 400:
                raise RuntimeError(f"Vanguard GraphQL returned HTTP {status}")
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Vanguard GraphQL HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Vanguard GraphQL connection failed: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Vanguard GraphQL connection failed: {e}") from e
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as e:
        raise RuntimeError(f"Vanguard GraphQL parse failed: {e}") from e
    if not isinstance(payload, dict):
        raise RuntimeError("Vanguard GraphQL payload is not an object")
    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"Vanguard GraphQL errors: {errors}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Vanguard GraphQL payload has no data")
    return data


def _fund_from_data(data: dict[str, Any]) -> dict[str, Any]:
    funds = data.get("funds")
    if not isinstance(funds, list) or not funds:
        raise RuntimeError("Vanguard GraphQL returned no fund")
    fund = funds[0]
    if not isinstance(fund, dict):
        raise RuntimeError("Vanguard GraphQL fund is not an object")
    return fund


def vanguard_product_ref_for_isin(isin: str) -> str | None:
    """Infer the Vanguard PortId for ``isin`` via the public GraphQL API."""
    key = isin.strip()
    if not key:
        return None
    if key in _VANGUARD_PRODUCT_REFS:
        return _VANGUARD_PRODUCT_REFS[key]
    ref: str | None = None
    try:
        data = _graphql_post(_VANGUARD_ISIN_QUERY, {"isins": [key]})
        funds = data.get("funds")
        if isinstance(funds, list):
            for fund in funds:
                if not isinstance(fund, dict):
                    continue
                raw_ref = fund.get("portId")
                if isinstance(raw_ref, (str, int)) and str(raw_ref).strip():
                    ref = str(raw_ref).strip()
                    break
    except RuntimeError as e:
        logger.warning("Vanguard product lookup failed for %s (%s)", key, e)
    _VANGUARD_PRODUCT_REFS[key] = ref
    return ref


def vanguard_product_url_exists(isin: str, product_ref: str | None = None) -> bool:
    """True when Vanguard returns the fund for ``isin`` and ``product_ref``."""
    key = isin.strip()
    if not key:
        return False
    cache_key = (key, product_ref)
    cached = _VANGUARD_PRODUCT_EXISTS.get(cache_key)
    if cached is not None:
        return cached
    ref = product_ref or vanguard_product_ref_for_isin(key)
    if not ref:
        _VANGUARD_PRODUCT_EXISTS[cache_key] = False
        return False
    exists = False
    try:
        data = _graphql_post(_VANGUARD_EXISTS_QUERY, {"portIds": [ref]})
        funds = data.get("funds")
        if isinstance(funds, list):
            for fund in funds:
                if not isinstance(fund, dict):
                    continue
                if str(fund.get("portId")) != ref:
                    continue
                profile = fund.get("profile")
                identifiers = (
                    profile.get("identifiers")
                    if isinstance(profile, dict)
                    else None
                )
                if isinstance(identifiers, list):
                    exists = any(
                        isinstance(identifier, dict)
                        and identifier.get("altIdValue") == key
                        for identifier in identifiers
                    )
                break
    except RuntimeError as e:
        logger.warning(
            "Vanguard product existence check failed for %s (%s)", key, e
        )
    _VANGUARD_PRODUCT_EXISTS[cache_key] = exists
    return exists


class VanguardPosition(JustETFPosition):
    """JustETF quotes with country and sector weights from Vanguard GraphQL."""

    _COUNTRY_ALIASES: dict[str, str] = {
        "Korea": "South Korea",
        "Russian Federation": "Russia",
    }
    _SECTOR_CANONICAL: dict[str, str] = {
        "Basic Materials": "Materials",
        "Telecommunications": "Telecommunication",
    }

    def __init__(
        self,
        isin: str,
        name: str | None = None,
        short_name: str | None = None,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        dmem_other: float | None = None,
        cached_countries: dict[str, float] | None = None,
        cached_sectors: dict[str, float] | None = None,
        price: float | None = None,
        prefer_scrape_value: bool = False,
        ctx: RuntimeContext | None = None,
    ) -> None:
        self._vanguard_fund: dict[str, Any] | None = None
        super().__init__(
            isin,
            name=name,
            short_name=short_name,
            shares=shares,
            value=value,
            broker=broker,
            dmem=dmem,
            usavn=usavn,
            dmem_other=dmem_other,
            cached_countries=cached_countries,
            cached_sectors=cached_sectors,
            price=price,
            prefer_scrape_value=prefer_scrape_value,
            ctx=ctx,
        )

    @staticmethod
    def _weight_pct(raw: object) -> float | None:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        if not isinstance(raw, str):
            return None
        stripped = (
            raw.strip().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
        )
        stripped = stripped.rstrip("%")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    @staticmethod
    def _country_stat_code(profile: dict[str, Any]) -> str:
        asset_class = profile.get("assetClassificationLevel1")
        primary = profile.get("primaryMarketEquityClassification")
        domicile = profile.get("marketOfDomicile")
        if asset_class == "Bond":
            return "CNTRYEMUG"
        if isinstance(primary, str):
            return "FTCTYATPCS" if "FTSE" in primary else "MSCTYATPCS"
        if primary is None and (
            domicile == "UNITED STATES" or asset_class == "Money Market"
        ):
            return "CNTRYATPCB"
        return "CNTRYEMUG"

    @classmethod
    def _countries_from_fund(
        cls, fund: dict[str, Any]
    ) -> list[dict[str, float | str]]:
        profile = fund.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        stat_code = cls._country_stat_code(profile)
        allocations = fund.get("marketAllocation")
        if not isinstance(allocations, list):
            return []
        weights: dict[str, float] = {}
        for row in allocations:
            if not isinstance(row, dict):
                continue
            if row.get("holdingStatCode") != stat_code:
                continue
            raw_name = row.get("countryName")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name = cls._COUNTRY_ALIASES.get(raw_name.strip(), raw_name.strip())
            weight = cls._weight_pct(row.get("fundMktPercent"))
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @classmethod
    def _sectors_from_fund(
        cls, fund: dict[str, Any]
    ) -> list[dict[str, float | str]]:
        sectors = fund.get("sectorDiversification")
        if not isinstance(sectors, list):
            return []
        raw_weights: dict[str, float] = {}
        for row in sectors:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("sectorName")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            weight = cls._weight_pct(row.get("fundPercent"))
            if weight is None or weight <= 0:
                continue
            name = raw_name.strip()
            raw_weights[name] = raw_weights.get(name, 0.0) + weight
        logger.info("Vanguard: detected raw sectors: %r", raw_weights)
        weights: dict[str, float] = {}
        for name, weight in raw_weights.items():
            canonical = cls._SECTOR_CANONICAL.get(name, name)
            canonical = JustETFPosition._canonical_sector_name(canonical)
            weights[canonical] = weights.get(canonical, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _fund_payload(self) -> dict[str, Any]:
        if self._vanguard_fund is not None:
            return self._vanguard_fund
        ref = self._ctx.isin_registry.get_product_ref_for_isin(self._isin)
        if not ref:
            ref = vanguard_product_ref_for_isin(self._isin)
        if not ref:
            raise RuntimeError(f"Vanguard PortId is unknown for {self._isin}")
        logger.info(
            "Vanguard: fetching allocation for %s (PortId %s)", self._isin, ref
        )
        data = _graphql_post(_VANGUARD_FUND_QUERY, {"portIds": [ref]})
        fund = _fund_from_data(data)
        if str(fund.get("portId")) != ref:
            raise RuntimeError(
                f"Vanguard PortId mismatch for {self._isin}: expected {ref}, "
                f"got {fund.get('portId')!r}"
            )
        self._vanguard_fund = fund
        return fund

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        fund = self._fund_payload()
        rows = self._countries_from_fund(fund)
        if not rows:
            logger.warning("Vanguard: no country weights for %s", self._isin)
        return rows

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        fund = self._fund_payload()
        rows = self._sectors_from_fund(fund)
        if rows:
            return rows
        logger.warning(
            "Vanguard: no sector weights for %s, falling back to JustETF",
            self._isin,
        )
        return super()._http_sector_dist_json()
