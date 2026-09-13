from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_DWS_PRODUCT_URL = "https://etf.dws.com/en-gb/{isin}"
_DWS_HOLDINGS_URL = "https://etf.dws.com/api/pdp/en-gb/etf/{slug}/holdings"
_DWS_LOCALE = "en-gb"
_DWS_EXISTS_TIMEOUT_S = 10
_DWS_FETCH_TIMEOUT_S = 30

# DWS / ISO labels -> names used in Position DMEM/USAVN lists.
_DWS_COUNTRY_ALIASES: dict[str, str] = {
    "Korea, Republic of": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea": "South Korea",
    "United States of America": "United States",
    "USA": "United States",
    "Russian Federation": "Russia",
    "Czechia": "Czech Republic",
    # DWS uses "--" for residual / unclassified holdings (the "Other" bucket).
    "--": "Other",
}

_DWS_PRODUCT_EXISTS: dict[str, bool] = {}


def _dws_product_url(isin: str) -> str:
    return _DWS_PRODUCT_URL.format(isin=isin)


def dws_product_url_exists(isin: str) -> bool:
    """True when ``https://etf.dws.com/en-gb/{isin}`` follows to a non-404 page."""
    cached = _DWS_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    url = _dws_product_url(isin)
    req = urllib.request.Request(
        url,
        headers=JustETFPosition._HEADERS,
        method="GET",
    )
    exists = False
    try:
        with urllib.request.urlopen(req, timeout=_DWS_EXISTS_TIMEOUT_S) as resp:
            exists = 200 <= getattr(resp, "status", 200) < 400
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("DWS product URL %s returned HTTP %s", url, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("DWS product URL check failed for %s (%s)", isin, e)
    _DWS_PRODUCT_EXISTS[isin] = exists
    return exists


class XtrackersPosition(JustETFPosition):
    """JustETF quotes with country weights from the DWS Xtrackers holdings API."""

    _DWS_API_HEADERS = {
        **JustETFPosition._HEADERS,
        "Accept": "application/json",
        "client-id": "passive-frontend",
        "Origin": "https://etf.dws.com",
    }

    @staticmethod
    def _slug_from_dws_product_url(final_url: str) -> str | None:
        path = urllib.parse.urlparse(final_url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or parts[0] != _DWS_LOCALE:
            return None
        slug = parts[-1]
        return slug or None

    @staticmethod
    def _field_value(row: dict[str, Any], key: str) -> Any:
        field = row.get(key)
        if isinstance(field, dict):
            return field.get("value")
        return None

    @staticmethod
    def _field_sort_value(row: dict[str, Any], key: str) -> float | None:
        field = row.get(key)
        if not isinstance(field, dict):
            return None
        raw = field.get("sortValue")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        text = field.get("value")
        if not isinstance(text, str):
            return None
        stripped = text.strip().replace(",", "").rstrip("%")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    @staticmethod
    def _countries_from_holdings_json(
        payload: dict[str, Any],
    ) -> list[dict[str, float | str]]:
        """Sum DWS holdings rows by ``column_3`` country into JustETF-shaped rows."""
        tables = payload.get("tables") or []
        if not tables:
            return []
        values = tables[0].get("values") or []
        weights: dict[str, float] = {}
        for row in values:
            if not isinstance(row, dict):
                continue
            raw_name = XtrackersPosition._field_value(row, "column_3")
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            name = _DWS_COUNTRY_ALIASES.get(name, name)
            weight = XtrackersPosition._field_sort_value(row, "column_1")
            if weight is None:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        try:
            slug, product_url = self._fetch_dws_slug()
            payload = self._http_holdings_json(slug, product_url)
            rows = self._countries_from_holdings_json(payload)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"DWS HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
            raise RuntimeError(
                f"DWS holdings parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning("DWS: no country weights in holdings for %s", self._isin)
        return rows

    def _fetch_dws_slug(self) -> tuple[str, str]:
        url = _dws_product_url(self._isin)
        req = urllib.request.Request(
            url,
            headers=self._HEADERS,
            method="GET",
        )
        logger.info("DWS: fetching product page %s", url)
        with urllib.request.urlopen(req, timeout=_DWS_FETCH_TIMEOUT_S) as resp:
            final_url = resp.geturl()
        slug = self._slug_from_dws_product_url(final_url)
        if not slug:
            raise RuntimeError(
                f"DWS product URL for {self._isin} did not yield a slug ({final_url})"
            )
        return slug, final_url

    def _http_holdings_json(self, slug: str, product_url: str) -> dict[str, Any]:
        url = _DWS_HOLDINGS_URL.format(slug=slug)
        headers = {
            **self._DWS_API_HEADERS,
            "Referer": product_url if product_url.endswith("/") else f"{product_url}/",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        logger.info("DWS: fetching holdings JSON from %s", url)
        with urllib.request.urlopen(req, timeout=_DWS_FETCH_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"DWS holdings JSON for {self._isin} is not an object"
            )
        return payload
