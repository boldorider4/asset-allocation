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
_INVESCO_ORIGIN = "https://www.invesco.com"
_INVESCO_LOCALE = "en_CH"
_INVESCO_EXISTS_TIMEOUT_S = 10
_INVESCO_FETCH_TIMEOUT_S = 30

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_MARKET_NAMES: tuple[str, ...] = tuple(
    _LIST_OF_DEVELOPED_MARKETS + _LIST_OF_EMERGING_MARKETS
)
_MARKET_BY_LOWER: dict[str, str] = {name.casefold(): name for name in _MARKET_NAMES}
_PYCOUNTRY_NAME_ALIASES: dict[str, str] = {
    "macao": "Macau",
}
_NON_COUNTRY_LABELS: frozenset[str] = frozenset(
    {"cash", "other", "n/a", "-", "--"}
)

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


def _content_type_is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "json" in content_type.lower()


def _http_country_json(isin: str, timeout_s: float) -> tuple[int, str | None, bytes]:
    req = urllib.request.Request(
        _invesco_country_url(isin),
        headers=_invesco_api_headers(),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") if resp.headers else None
        payload = resp.read()
    return status, content_type, payload


def invesco_product_url_exists(isin: str) -> bool:
    """True when the Invesco dng-api returns country weights for ``isin``."""
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
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            exists = InvescoPosition._payload_matches_isin(payload, isin)
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
    """JustETF quotes with country weights from the Invesco dng-api."""

    ISINS: frozenset[str] = frozenset(
        {
            "IE00BKS7L097",
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
            aliased = _PYCOUNTRY_NAME_ALIASES.get(candidate.casefold())
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
        try:
            record = pycountry.countries.lookup(spaced)
        except LookupError:
            record = None
        if record is not None:
            return InvescoPosition._name_for_market_lists(record)
        return spaced

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

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        url = _invesco_country_url(self._isin)
        logger.info("Invesco: fetching country exposure from %s", url)
        try:
            status, content_type, raw = _http_country_json(
                self._isin, timeout_s=_INVESCO_FETCH_TIMEOUT_S
            )
            if not (200 <= status < 400):
                raise RuntimeError(
                    f"Invesco HTTP {status} while fetching countries for {self._isin}"
                )
            if not _content_type_is_json(content_type):
                raise RuntimeError(
                    f"Invesco holdings for {self._isin} is not JSON ({content_type})"
                )
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"Invesco holdings JSON for {self._isin} is not an object"
                )
            rows = InvescoPosition._countries_from_holdings_json(payload, self._isin)
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
                "Invesco: no country weights in holdingWeights for %s", self._isin
            )
        return rows
