from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_AMUNDI_PRODUCTS_URL = "https://www.amundietf.de/mapi/ProductAPI/getProductsData"
_AMUNDI_ORIGIN = "https://www.amundietf.de"
_AMUNDI_EXISTS_TIMEOUT_S = 10
_AMUNDI_FETCH_TIMEOUT_S = 30

# Amundi country labels -> names used in Position DMEM/USAVN lists.
_AMUNDI_COUNTRY_ALIASES: dict[str, str] = {
    "Korea, Republic of": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea": "South Korea",
    "Russian Federation": "Russia",
}

_AMUNDI_PRODUCT_EXISTS: dict[str, bool] = {}


def _amundi_request_body(isin: str, *, include_countries: bool, include_sectors: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": {
            "countryCode": "DEU",
            "countryName": "Germany",
            "languageCode": "en",
            "languageName": "English",
            "userProfileName": "RETAIL",
        },
        "productIds": [isin],
        "characteristics": ["ISIN"],
        "productType": "PRODUCT",
        "historics": [],
    }
    fields: list[str] = []
    if include_countries:
        fields.append("FUND_COUNTRIES")
    if include_sectors:
        fields.append("FUND_SECTORS")
    if fields:
        body["breakDown"] = {"aggregationFields": fields}
    return body


def _amundi_api_headers() -> dict[str, str]:
    return {
        **JustETFPosition._HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": _AMUNDI_ORIGIN,
        "Referer": f"{_AMUNDI_ORIGIN}/",
    }


def _content_type_is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "json" in content_type.lower()


def _post_amundi_products(
    isin: str, *, include_countries: bool, include_sectors: bool, timeout_s: float
) -> tuple[int, str | None, bytes]:
    if not isin:
        raise ValueError("Amundi productIds must not be empty")
    body = json.dumps(
        _amundi_request_body(isin, include_countries=include_countries, include_sectors=include_sectors)
    ).encode()
    req = urllib.request.Request(
        _AMUNDI_PRODUCTS_URL,
        data=body,
        headers=_amundi_api_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") if resp.headers else None
        payload = resp.read()
    return status, content_type, payload


def amundi_product_url_exists(isin: str) -> bool:
    """True when the Amundi ProductAPI returns a matching product for ``isin``."""
    cached = _AMUNDI_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    if not isin:
        _AMUNDI_PRODUCT_EXISTS[isin] = False
        return False
    exists = False
    try:
        status, content_type, raw = _post_amundi_products(
            isin, include_countries=False, include_sectors=False, timeout_s=_AMUNDI_EXISTS_TIMEOUT_S
        )
        if 200 <= status < 400 and _content_type_is_json(content_type):
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                exists = AmundiPosition._select_product(payload, isin) is not None
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("Amundi ProductAPI for %s returned HTTP %s", isin, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("Amundi ProductAPI check failed for %s (%s)", isin, e)
    except OSError as e:
        exists = False
        logger.warning("Amundi ProductAPI check connection failed for %s (%s)", isin, e)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as e:
        exists = False
        logger.warning("Amundi ProductAPI check parse failed for %s (%s)", isin, e)
    _AMUNDI_PRODUCT_EXISTS[isin] = exists
    return exists


class AmundiPosition(JustETFPosition):
    """JustETF quotes with country and sector weights from the Amundi ProductAPI."""

    ISINS: frozenset[str] = frozenset(
        {
            "IE000BI8OT95",
            "LU2233156582",
            "LU2300294316",
        }
    )

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
        ctx: Any = None,
    ) -> None:
        self._breakdowns_payload: dict[str, Any] | None = None
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
    def _select_product(payload: dict[str, Any], isin: str) -> dict[str, Any] | None:
        products = payload.get("products") or []
        wanted = isin.casefold()
        for product in products:
            if not isinstance(product, dict):
                continue
            product_id = product.get("productId")
            if isinstance(product_id, str) and product_id.casefold() == wanted:
                return product
            characteristics = product.get("characteristics")
            if isinstance(characteristics, dict):
                char_isin = characteristics.get("ISIN")
                if isinstance(char_isin, str) and char_isin.casefold() == wanted:
                    return product
        return None

    @staticmethod
    def _weight_to_pct(raw: Any) -> float | None:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw) * 100.0
        if not isinstance(raw, str):
            return None
        stripped = raw.strip().replace(",", "").rstrip("%")
        if not stripped:
            return None
        try:
            return float(stripped) * 100.0
        except ValueError:
            return None

    @staticmethod
    def _countries_from_products_json(
        payload: dict[str, Any],
        isin: str,
    ) -> list[dict[str, float | str]]:
        """Read FUND_COUNTRIES into JustETF-shaped rows."""
        product = AmundiPosition._select_product(payload, isin)
        if product is None:
            return []
        breakdowns = product.get("breakDowns") or []
        rows: list[Any] = []
        for breakdown in breakdowns:
            if not isinstance(breakdown, dict):
                continue
            if breakdown.get("aggregationField") != "FUND_COUNTRIES":
                continue
            data = breakdown.get("breakDownData") or []
            if isinstance(data, list):
                rows = data
            break
        weights: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("aggregationName")
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            name = _AMUNDI_COUNTRY_ALIASES.get(name, name)
            weight = AmundiPosition._weight_to_pct(row.get("weight"))
            if weight is None:
                weight = AmundiPosition._weight_to_pct(row.get("adjustedWeight"))
            if weight is None:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @staticmethod
    def _sectors_from_products_json(
        payload: dict[str, Any],
        isin: str,
    ) -> list[dict[str, float | str]]:
        """Read FUND_SECTORS into JustETF-shaped rows."""
        product = AmundiPosition._select_product(payload, isin)
        if product is None:
            return []
        breakdowns = product.get("breakDowns") or []
        for breakdown in breakdowns:
            if not isinstance(breakdown, dict):
                continue
            if breakdown.get("aggregationField") != "FUND_SECTORS":
                continue
            data = breakdown.get("breakDownData") or []
            if not data:
                continue
            weights: dict[str, float] = {}
            for row in data:
                if not isinstance(row, dict):
                    continue
                raw_name = row.get("aggregationName")
                if not isinstance(raw_name, str):
                    continue
                name = raw_name.strip()
                if not name:
                    continue
                weight = AmundiPosition._weight_to_pct(row.get("weight"))
                if weight is None:
                    weight = AmundiPosition._weight_to_pct(row.get("adjustedWeight"))
                if weight is None:
                    continue
                canonical = JustETFPosition._canonical_sector_name(name)
                weights[canonical] = weights.get(canonical, 0.0) + weight
            if weights:
                return [
                    {"name": name, "weight_pct": weight}
                    for name, weight in sorted(weights.items(), key=lambda item: -item[1])
                ]
        return []

    def _fetch_combined_breakdowns(
        self, *, include_countries: bool, include_sectors: bool
    ) -> dict[str, Any]:
        """Fetch breakdowns in one POST; cache full payload on the instance."""
        if not include_countries and not include_sectors:
            return {}

        if self._breakdowns_payload is not None:
            cached_fields: set[str] = set()
            for bd in (self._breakdowns_payload.get("products") or [{}])[0].get("breakDowns", []):
                cached_fields.add(bd.get("aggregationField", ""))
            needed: set[str] = set()
            if include_countries:
                needed.add("FUND_COUNTRIES")
            if include_sectors:
                needed.add("FUND_SECTORS")
            if needed.issubset(cached_fields):
                return self._breakdowns_payload

        status, content_type, raw = _post_amundi_products(
            self._isin,
            include_countries=include_countries,
            include_sectors=include_sectors,
            timeout_s=_AMUNDI_FETCH_TIMEOUT_S,
        )
        if not (200 <= status < 400):
            raise RuntimeError(
                f"Amundi HTTP {status} while fetching breakdowns for {self._isin}"
            )
        if not _content_type_is_json(content_type):
            raise RuntimeError(
                f"Amundi products for {self._isin} is not JSON ({content_type})"
            )
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Amundi products JSON for {self._isin} is not an object"
            )
        self._breakdowns_payload = payload
        return payload

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        logger.info("Amundi: fetching FUND_COUNTRIES for %s", self._isin)
        try:
            payload = self._fetch_combined_breakdowns(
                include_countries=True, include_sectors=self._ctx.config.fetch_sectorsplit if self._ctx else False
            )
            rows = self._countries_from_products_json(payload, self._isin)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Amundi HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"Amundi connection failed while fetching countries for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"Amundi products parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning("Amundi: no country weights in FUND_COUNTRIES for %s", self._isin)
        return rows

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        logger.info("Amundi: fetching FUND_SECTORS for %s", self._isin)
        try:
            payload = self._fetch_combined_breakdowns(
                include_countries=self._ctx.config.fetch_geosplit if self._ctx else False,
                include_sectors=True,
            )
            rows = self._sectors_from_products_json(payload, self._isin)
            if rows:
                return rows
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Amundi HTTP {e.code} while fetching sectors for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"Amundi connection failed while fetching sectors for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"Amundi products parse failed for {self._isin}: {e}"
            ) from e
        logger.warning("Amundi: FUND_SECTORS empty for %s, falling back to JustETF", self._isin)
        return super()._http_sector_dist_json()