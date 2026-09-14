from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

import pycountry

from logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition
from position.position import (
    _LIST_OF_DEVELOPED_MARKETS,
    _LIST_OF_EMERGING_MARKETS,
    _OTHER_MARKET_NAME,
)

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_INVESCO_COUNTRY_URL = (
    "https://dng-api.invesco.com/cache/v1/accounts/{locale}/shareclasses/{isin}"
    "/weightedHoldings/index?idType=isin&breakdown=country"
)
_INVESCO_HOLDINGS_URL = (
    "https://dng-api.invesco.com/cache/v1/accounts/{locale}/shareclasses/{isin}"
    "/holdings/index?idType=isin"
)
_INVESCO_ORIGIN = "https://www.invesco.com"
_INVESCO_LOCALE = "en_CH"
_INVESCO_EXISTS_TIMEOUT_S = 10
_INVESCO_FETCH_TIMEOUT_S = 30

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_MARKET_NAMES: tuple[str, ...] = tuple(
    _LIST_OF_DEVELOPED_MARKETS + _LIST_OF_EMERGING_MARKETS
)
_MARKET_BY_LOWER: dict[str, str] = {name.casefold(): name for name in _MARKET_NAMES}
_NAME_ALIASES: dict[str, str] = {
    "macao": "Macau",
}
_NON_COUNTRY_LABELS: frozenset[str] = frozenset(
    {"cash", "other", "n/a", "-", "--"}
)
_ISIN_SPECIAL_PREFIXES: frozenset[str] = frozenset(
    {
        "QS",
        "QT",
        "QW",
        "XA",
        "XB",
        "XC",
        "XD",
        "XF",
        "XS",
        "XT",
        "XX",
    }
)
_ISIN_PREFIX_TO_MARKET: dict[str, str] = {
    "EU": "European Union",
    "UK": "United Kingdom",
}

_INVESCO_PRODUCT_EXISTS: dict[str, bool] = {}


def _invesco_api_headers() -> dict[str, str]:
    return {
        **JustETFPosition._HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Origin": _INVESCO_ORIGIN,
        "Referer": f"{_INVESCO_ORIGIN}/ch/en/",
    }


def _invesco_country_url(isin: str) -> str:
    return _INVESCO_COUNTRY_URL.format(locale=_INVESCO_LOCALE, isin=isin)


def _invesco_holdings_url(isin: str) -> str:
    return _INVESCO_HOLDINGS_URL.format(locale=_INVESCO_LOCALE, isin=isin)


def _content_type_is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "json" in content_type.lower()


def _http_json(url: str, timeout_s: float) -> tuple[int, str | None, bytes]:
    req = urllib.request.Request(
        url,
        headers=_invesco_api_headers(),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") if resp.headers else None
        payload = resp.read()
    return status, content_type, payload


def _http_country_json(isin: str, timeout_s: float) -> tuple[int, str | None, bytes]:
    return _http_json(_invesco_country_url(isin), timeout_s)


def _http_holdings_json(isin: str, timeout_s: float) -> tuple[int, str | None, bytes]:
    return _http_json(_invesco_holdings_url(isin), timeout_s)


def _decode_object(raw: bytes) -> dict[str, object] | None:
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else None


def invesco_product_url_exists(isin: str) -> bool:
    """True when Invesco has index country weights or index holdings for ``isin``."""
    cached = _INVESCO_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    if not isin:
        _INVESCO_PRODUCT_EXISTS[isin] = False
        return False
    exists = False
    try:
        status, content_type, raw = _http_country_json(
            isin, timeout_s=_INVESCO_EXISTS_TIMEOUT_S
        )
        if 200 <= status < 400 and _content_type_is_json(content_type):
            payload = _decode_object(raw)
            if payload is not None:
                exists = bool(
                    InvescoPosition._countries_from_holdings_json(payload, isin)
                )
        if not exists:
            status, content_type, raw = _http_holdings_json(
                isin, timeout_s=_INVESCO_EXISTS_TIMEOUT_S
            )
            if 200 <= status < 400 and _content_type_is_json(content_type):
                payload = _decode_object(raw)
                if payload is not None:
                    exists = bool(
                        InvescoPosition._countries_from_constituents_json(payload)
                    )
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("Invesco dng-api for %s returned HTTP %s", isin, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("Invesco dng-api check failed for %s (%s)", isin, e)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as e:
        exists = False
        logger.warning("Invesco dng-api check parse failed for %s (%s)", isin, e)
    _INVESCO_PRODUCT_EXISTS[isin] = exists
    return exists


class InvescoPosition(JustETFPosition):
    """JustETF quotes with country weights from Invesco index or holdings JSON."""

    ISINS: frozenset[str] = frozenset(
        {
            "IE00BKS7L097",
            "IE000PJL7R74",
        }
    )

    @staticmethod
    def _payload_matches_isin(payload: object, isin: str) -> bool:
        if not isinstance(payload, dict):
            return False
        raw_isin = payload.get("isin")
        if not isinstance(raw_isin, str) or raw_isin.casefold() != isin.casefold():
            return False
        weights = payload.get("holdingWeights")
        return isinstance(weights, list)

    @staticmethod
    def _pycountry_name_candidates(record: object) -> list[str]:
        names: list[str] = []
        for attr in ("common_name", "name", "official_name"):
            value = getattr(record, attr, None)
            if isinstance(value, str) and value and value not in names:
                names.append(value)
        return names

    @staticmethod
    def _name_for_market_lists(record: object) -> str:
        candidates = InvescoPosition._pycountry_name_candidates(record)
        for candidate in candidates:
            listed = _MARKET_BY_LOWER.get(candidate.casefold())
            if listed:
                return listed
            aliased = _NAME_ALIASES.get(candidate.casefold())
            if aliased is not None:
                return aliased
        return candidates[0] if candidates else _OTHER_MARKET_NAME

    @staticmethod
    def _display_country(raw_name: str) -> str:
        spaced = _CAMEL_BOUNDARY.sub(" ", raw_name.strip())
        if not spaced or spaced.casefold() in _NON_COUNTRY_LABELS:
            return _OTHER_MARKET_NAME
        listed = _MARKET_BY_LOWER.get(spaced.casefold())
        if listed:
            return listed
        aliased = _NAME_ALIASES.get(spaced.casefold())
        if aliased is not None:
            return aliased
        return spaced

    @staticmethod
    def _country_from_isin(raw_isin: str) -> str | None:
        code = raw_isin.strip()[:2].upper()
        if len(code) != 2 or not code.isalpha():
            return None
        if code in _ISIN_SPECIAL_PREFIXES:
            return _OTHER_MARKET_NAME
        aliased = _ISIN_PREFIX_TO_MARKET.get(code)
        if aliased is not None:
            return aliased
        record = pycountry.countries.get(alpha_2=code)
        if record is None:
            record = pycountry.historic_countries.get(alpha_2=code)
        if record is None:
            return _OTHER_MARKET_NAME
        return InvescoPosition._name_for_market_lists(record)

    @staticmethod
    def _weight_pct(raw: object) -> float | None:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        if not isinstance(raw, str):
            return None
        stripped = raw.strip().replace(",", "").rstrip("%")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    @staticmethod
    def _countries_from_holdings_json(
        payload: dict[str, object],
        isin: str,
    ) -> list[dict[str, float | str]]:
        """Read holdingWeights country rows into JustETF-shaped rows."""
        if not InvescoPosition._payload_matches_isin(payload, isin):
            return []
        rows = payload.get("holdingWeights")
        if not isinstance(rows, list):
            return []
        weights: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name = InvescoPosition._display_country(raw_name)
            weight = InvescoPosition._weight_pct(row.get("value"))
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @staticmethod
    def _countries_from_constituents_json(
        payload: dict[str, object],
    ) -> list[dict[str, float | str]]:
        """Sum index holdings by ISIN country prefix."""
        rows = payload.get("holdings")
        if not isinstance(rows, list):
            return []
        weights: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_isin = row.get("isin")
            if not isinstance(raw_isin, str) or not raw_isin.strip():
                continue
            name = InvescoPosition._country_from_isin(raw_isin)
            if not name:
                continue
            weight = InvescoPosition._weight_pct(row.get("weight"))
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _load_json_object(self, url: str) -> dict[str, object]:
        logger.info("Invesco: fetching %s", url)
        status, content_type, raw = _http_json(url, _INVESCO_FETCH_TIMEOUT_S)
        if not (200 <= status < 400):
            raise RuntimeError(
                f"Invesco HTTP {status} while fetching countries for {self._isin}"
            )
        if not _content_type_is_json(content_type):
            raise RuntimeError(
                f"Invesco payload for {self._isin} is not JSON ({content_type})"
            )
        payload = _decode_object(raw)
        if payload is None:
            raise RuntimeError(
                f"Invesco JSON for {self._isin} is not an object"
            )
        return payload

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        try:
            country_payload = self._load_json_object(_invesco_country_url(self._isin))
            rows = InvescoPosition._countries_from_holdings_json(
                country_payload, self._isin
            )
            if not rows:
                logger.info(
                    "Invesco: no index country weights for %s; using holdings ISINs",
                    self._isin,
                )
                holdings_payload = self._load_json_object(
                    _invesco_holdings_url(self._isin)
                )
                rows = InvescoPosition._countries_from_constituents_json(
                    holdings_payload
                )
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Invesco HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"Invesco holdings parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning(
                "Invesco: no country weights from index or holdings for %s",
                self._isin,
            )
        return rows
