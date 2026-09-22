# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition
from position.position import (
    _LIST_OF_DEVELOPED_MARKETS,
    _LIST_OF_EMERGING_MARKETS,
    _OTHER_MARKET_NAME,
)

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_SSGA_PRODUCT_URL = (
    "https://www.ssga.com/de/en_gb/intermediary/etfs/{slug}"
)
_SSGA_EXISTS_TIMEOUT_S = 10
_SSGA_FETCH_TIMEOUT_S = 30

_GEO_INPUT_RE = re.compile(
    r'id="fund-geographical-breakdown"\s+value="([^"]*)"',
    re.IGNORECASE,
)
_SECTOR_INPUT_RE = re.compile(
    r'id="fund-sector-breakdown"\s+value="([^"]*)"',
    re.IGNORECASE,
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_MARKET_NAMES: tuple[str, ...] = tuple(
    _LIST_OF_DEVELOPED_MARKETS + _LIST_OF_EMERGING_MARKETS
)
_MARKET_BY_LOWER: dict[str, str] = {name.casefold(): name for name in _MARKET_NAMES}
_NAME_ALIASES: dict[str, str] = {
    "korea, republic of": "South Korea",
    "republic of korea": "South Korea",
    "korea (south)": "South Korea",
    "korea": "South Korea",
    "united states of america": "United States",
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "russian federation": "Russian Federation",
    "macao": "Macau",
}
_NON_COUNTRY_LABELS: frozenset[str] = frozenset(
    {"cash", "other", "n/a", "-", "--", "unclassified", "unassigned"}
)

# ISIN -> SSGA product slug on the DE intermediary ETF site.
_SSGA_PRODUCT_SLUGS: dict[str, str] = {
    "IE00B4YBJ215": "state-street-spdr-sp-400-us-mid-cap-ucits-etf-acc-spy4-gy",
}

_SSGA_PRODUCT_EXISTS: dict[str, bool] = {}


def _ssga_product_url(isin: str) -> str | None:
    slug = _SSGA_PRODUCT_SLUGS.get(isin)
    if not slug:
        return None
    return _SSGA_PRODUCT_URL.format(slug=slug)


def _ssga_html_headers() -> dict[str, str]:
    return {
        **JustETFPosition._HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }


def _http_product_html(url: str, timeout_s: float) -> tuple[int, str | None, bytes]:
    req = urllib.request.Request(url, headers=_ssga_html_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") if resp.headers else None
        payload = resp.read()
    return status, content_type, payload


def ssga_product_url_exists(isin: str) -> bool:
    """True when the SSGA product page embeds geographical weights for ``isin``."""
    cached = _SSGA_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    url = _ssga_product_url(isin)
    if not url:
        _SSGA_PRODUCT_EXISTS[isin] = False
        return False
    exists = False
    try:
        status, _content_type, raw = _http_product_html(url, _SSGA_EXISTS_TIMEOUT_S)
        if 200 <= status < 400:
            html_text = raw.decode("utf-8", errors="replace")
            payload = StateStreetPosition._geo_payload_from_html(html_text)
            exists = bool(
                payload
                and StateStreetPosition._countries_from_geo_json(payload)
            )
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("SSGA product page for %s returned HTTP %s", isin, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("SSGA product page check failed for %s (%s)", isin, e)
    except OSError as e:
        # Read timeouts, resets, DNS/SSL failures: bail to cached data.
        exists = False
        logger.warning("SSGA product page check connection failed for %s (%s)", isin, e)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as e:
        exists = False
        logger.warning("SSGA product page parse failed for %s (%s)", isin, e)
    _SSGA_PRODUCT_EXISTS[isin] = exists
    return exists


class StateStreetPosition(JustETFPosition):
    """JustETF quotes with country and sector weights from the SSGA page."""

    ISINS: frozenset[str] = frozenset(_SSGA_PRODUCT_SLUGS)

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
        self._html_payload: str | None = None
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
    def _geo_payload_from_html(html_text: str) -> dict[str, object] | None:
        match = _GEO_INPUT_RE.search(html_text)
        if not match:
            return None
        raw = html.unescape(match.group(1))
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _sector_payload_from_html(html_text: str) -> dict[str, object] | None:
        match = _SECTOR_INPUT_RE.search(html_text)
        if not match:
            return None
        raw = html.unescape(match.group(1))
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _sectors_from_geo_json(
        payload: dict[str, object],
    ) -> list[dict[str, float | str]]:
        """Read sector attrArray into JustETF-shaped rows."""
        rows = payload.get("attrArray")
        if not isinstance(rows, list):
            return []
        raw_weights: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_name = StateStreetPosition._row_name(row)
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            weight = StateStreetPosition._row_weight(row)
            if weight is None or weight <= 0:
                continue
            raw_weights[raw_name.strip()] = raw_weights.get(raw_name.strip(), 0.0) + weight
        logger.info("StateStreet: detected raw sectors: %r", raw_weights)
        weights: dict[str, float] = {}
        for name, weight in raw_weights.items():
            # Map to canonical sector names (Cash -> Other)
            canonical = JustETFPosition._canonical_sector_name(name)
            weights[canonical] = weights.get(canonical, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

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
    def _row_weight(row: dict[str, object]) -> float | None:
        weight = row.get("weight")
        if isinstance(weight, dict):
            original = StateStreetPosition._weight_pct(weight.get("originalValue"))
            if original is not None:
                return original
            return StateStreetPosition._weight_pct(weight.get("value"))
        return StateStreetPosition._weight_pct(weight)

    @staticmethod
    def _row_name(row: dict[str, object]) -> str | None:
        name = row.get("name")
        if isinstance(name, dict):
            value = name.get("value")
            return value if isinstance(value, str) else None
        return name if isinstance(name, str) else None

    @staticmethod
    def _countries_from_geo_json(
        payload: dict[str, object],
    ) -> list[dict[str, float | str]]:
        rows = payload.get("attrArray")
        if not isinstance(rows, list):
            return []
        weights: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_name = StateStreetPosition._row_name(row)
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name = StateStreetPosition._display_country(raw_name)
            weight = StateStreetPosition._row_weight(row)
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        url = _ssga_product_url(self._isin)
        if not url:
            raise RuntimeError(f"SSGA product URL is unknown for {self._isin}")
        logger.info("SSGA: fetching geographical weights from %s", url)
        try:
            if self._html_payload is not None:
                html_text = self._html_payload
                payload = StateStreetPosition._geo_payload_from_html(html_text)
            else:
                status, _content_type, raw = _http_product_html(
                    url, _SSGA_FETCH_TIMEOUT_S
                )
                if not (200 <= status < 400):
                    raise RuntimeError(
                        f"SSGA HTTP {status} while fetching countries for {self._isin}"
                    )
                html_text = raw.decode("utf-8", errors="replace")
                payload = StateStreetPosition._geo_payload_from_html(html_text)
                if payload is None:
                    raise RuntimeError(
                        f"SSGA geographical JSON missing for {self._isin}"
                    )
                self._html_payload = html_text
            rows = StateStreetPosition._countries_from_geo_json(payload)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"SSGA HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"SSGA connection failed while fetching countries for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"SSGA geographical parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning(
                "SSGA: no country weights in geographical JSON for %s", self._isin
            )
        return rows

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        logger.info("SSGA: fetching sector breakdown for %s", self._isin)
        try:
            if self._html_payload is not None:
                payload = StateStreetPosition._sector_payload_from_html(self._html_payload)
            else:
                url = _ssga_product_url(self._isin)
                if not url:
                    raise RuntimeError(f"SSGA product URL is unknown for {self._isin}")
                status, _content_type, raw = _http_product_html(
                    url, _SSGA_FETCH_TIMEOUT_S
                )
                if not (200 <= status < 400):
                    raise RuntimeError(
                        f"SSGA HTTP {status} while fetching sectors for {self._isin}"
                    )
                html_text = raw.decode("utf-8", errors="replace")
                payload = StateStreetPosition._sector_payload_from_html(html_text)
                self._html_payload = html_text

            if payload is not None:
                rows = StateStreetPosition._sectors_from_geo_json(payload)
                if rows:
                    return rows
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"SSGA HTTP {e.code} while fetching sectors for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"SSGA connection failed while fetching sectors for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"SSGA sector parse failed for {self._isin}: {e}"
            ) from e
        logger.warning("SSGA: no sector weights for %s, falling back to JustETF", self._isin)
        return super()._http_sector_dist_json()
