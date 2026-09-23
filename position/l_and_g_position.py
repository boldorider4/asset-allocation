# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from cli.logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition
from position.position import (
    _LIST_OF_DEVELOPED_MARKETS,
    _LIST_OF_EMERGING_MARKETS,
    _OTHER_MARKET_NAME,
)

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_LANDG_ORIGIN = "https://fundcentres.landg.com"
_LANDG_LISTING_URL = (
    f"{_LANDG_ORIGIN}/srp/api/fund-centre/{{centre}}"
    "?audience={audience}&language={language}"
)
_LANDG_PART_URL = (
    _LANDG_ORIGIN
    + "/srp/api/part?id={part_id}&audience={audience}"
    + "&route={route}&version=live&languageId={language}"
    + "&fund_id={fund_id}&share_class_id={share_class_id}"
)
# Shared ETF fund-page canvas: Portfolio → Currency/Country → Country (%).
_LANDG_PORTFOLIO_PART_ID = 12618
# Sector breakdown canvas on the same fund page.
_LANDG_SECTOR_PART_ID = 12035
_LANDG_EXISTS_TIMEOUT_S = 10
_LANDG_FETCH_TIMEOUT_S = 30

# DE adviser ETF centre first; UK ETF centre as fallback.
_LANDG_CENTRES: tuple[dict[str, int], ...] = (
    {"centre": 48, "audience": 148, "route": 6696, "language": 1},
    {"centre": 47, "audience": 146, "route": 6696, "language": 1},
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_COUNTRY_DATA_RE = re.compile(
    r'data-key="country"[^>]*>[\s\S]*?'
    r'<script type="application/json" class="data">\s*(\[[\s\S]*?\])\s*</script>',
    re.IGNORECASE,
)
_SECTOR_DATA_RE = re.compile(
    r'data-key="sector"[^>]*>[\s\S]*?'
    r'<script type="application/json" class="data">\s*(\[[\s\S]*?\])\s*</script>',
    re.IGNORECASE,
)

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

_LANDG_PRODUCT_EXISTS: dict[str, bool] = {}
_LANDG_SHARECLASS: dict[str, dict[str, int]] = {}


def _landg_headers(*, accept: str) -> dict[str, str]:
    return {
        **JustETFPosition._HEADERS,
        "Accept": accept,
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": f"{_LANDG_ORIGIN}/en/de/adviser-wealth/fund-centre/etf/",
    }


def _http_get(url: str, timeout_s: float, accept: str) -> tuple[int, str | None, bytes]:
    req = urllib.request.Request(
        url, headers=_landg_headers(accept=accept), method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type") if resp.headers else None
        payload = resp.read()
    return status, content_type, payload


def _field_index(fields: object, code_name: str) -> int | None:
    if not isinstance(fields, list):
        return None
    for index, field in enumerate(fields):
        if isinstance(field, dict) and field.get("code_name") == code_name:
            return index
    return None


def _listing_shareclass(
    payload: dict[str, object], isin: str, centre: dict[str, int]
) -> dict[str, int] | None:
    metadata = payload.get("metadata")
    funds = payload.get("funds")
    if not isinstance(metadata, dict) or not isinstance(funds, list):
        return None
    isin_index = _field_index(metadata.get("share_class_fields"), "shareclassISIN")
    if isin_index is None:
        return None
    for fund in funds:
        if not isinstance(fund, dict):
            continue
        fund_id = fund.get("id")
        share_classes = fund.get("share_classes")
        if not isinstance(fund_id, int) or not isinstance(share_classes, list):
            continue
        for share_class in share_classes:
            if not isinstance(share_class, dict):
                continue
            rows = share_class.get("data")
            share_class_id = share_class.get("id")
            if not isinstance(share_class_id, int) or not isinstance(rows, list):
                continue
            if isin_index >= len(rows) or rows[isin_index] != isin:
                continue
            return {
                "fund_id": fund_id,
                "share_class_id": share_class_id,
                "audience": centre["audience"],
                "route": centre["route"],
                "language": centre["language"],
                "part_id": _LANDG_PORTFOLIO_PART_ID,
            }
    return None


def _resolve_shareclass(isin: str, timeout_s: float) -> dict[str, int] | None:
    cached = _LANDG_SHARECLASS.get(isin)
    if cached is not None:
        return cached
    for centre in _LANDG_CENTRES:
        url = _LANDG_LISTING_URL.format(**centre)
        logger.info("L&G: listing funds from %s", url)
        status, _content_type, raw = _http_get(
            url, timeout_s, "application/json, text/plain, */*"
        )
        if not (200 <= status < 400):
            continue
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            continue
        found = _listing_shareclass(payload, isin, centre)
        if found is not None:
            _LANDG_SHARECLASS[isin] = found
            return found
    return None


def _part_url(ids: dict[str, int]) -> str:
    return _LANDG_PART_URL.format(**ids)


def landg_product_url_exists(isin: str) -> bool:
    """True when the L&G listing API plus Country (%) canvas have weights."""
    cached = _LANDG_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    if not isin:
        _LANDG_PRODUCT_EXISTS[isin] = False
        return False
    exists = False
    try:
        ids = _resolve_shareclass(isin, _LANDG_EXISTS_TIMEOUT_S)
        if ids is not None:
            status, _content_type, raw = _http_get(
                _part_url(ids),
                _LANDG_EXISTS_TIMEOUT_S,
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            if 200 <= status < 400:
                html_text = raw.decode("utf-8", errors="replace")
                exists = bool(LAndGPosition._countries_from_portfolio_html(html_text))
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("L&G product check for %s returned HTTP %s", isin, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("L&G product check failed for %s (%s)", isin, e)
    except OSError as e:
        # Read timeouts, resets, DNS/SSL failures: bail to cached data.
        exists = False
        logger.warning("L&G product check connection failed for %s (%s)", isin, e)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as e:
        exists = False
        logger.warning("L&G product parse failed for %s (%s)", isin, e)
    _LANDG_PRODUCT_EXISTS[isin] = exists
    return exists


class LAndGPosition(JustETFPosition):
    """JustETF quotes with country and sector weights from the L&G fund-centre."""

    ISINS: frozenset[str] = frozenset({"IE000Z9UVQ99", "IE00BFXR5W90"})

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
    def _pairs_from_country_json(raw: str) -> list[tuple[str, float]]:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []
        pairs: list[tuple[str, float]] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 2:
                continue
            name, weight_raw = row[0], row[1]
            if not isinstance(name, str) or not name.strip():
                continue
            weight = LAndGPosition._weight_pct(weight_raw)
            if weight is None or weight <= 0:
                continue
            pairs.append((LAndGPosition._display_country(name), weight))
        return pairs

    @staticmethod
    def _countries_from_portfolio_html(
        html_text: str,
    ) -> list[dict[str, float | str]]:
        weights: dict[str, float] = {}
        for match in _COUNTRY_DATA_RE.finditer(html_text):
            pairs = LAndGPosition._pairs_from_country_json(match.group(1))
            if pairs:
                for name, weight in pairs:
                    weights[name] = weights.get(name, 0.0) + weight
                break
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        try:
            ids = _resolve_shareclass(self._isin, _LANDG_FETCH_TIMEOUT_S)
            if ids is None:
                raise RuntimeError(f"L&G listing has no share class for {self._isin}")
            url = _part_url(ids)
            logger.info("L&G: fetching Country (%%) canvas from %s", url)
            status, _content_type, raw = _http_get(
                url,
                _LANDG_FETCH_TIMEOUT_S,
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            if not (200 <= status < 400):
                raise RuntimeError(
                    f"L&G HTTP {status} while fetching countries for {self._isin}"
                )
            rows = LAndGPosition._countries_from_portfolio_html(
                raw.decode("utf-8", errors="replace")
            )
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"L&G HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"L&G connection failed while fetching countries for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"L&G portfolio parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning("L&G: no Country (%%) weights for %s", self._isin)
        return rows

    @staticmethod
    def _display_sector(raw_name: str) -> str:
        """Map raw sector name to canonical staple sector name."""
        return JustETFPosition._canonical_sector_name(raw_name.strip())

    @staticmethod
    def _pairs_from_sector_json(raw: str) -> list[tuple[str, float]]:
        """Parse sector JSON rows (same structure as country JSON)."""
        payload = json.loads(raw)
        if not isinstance(payload, list):
            return []
        raw_pairs: list[tuple[str, float]] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 2:
                continue
            name, weight_raw = row[0], row[1]
            if not isinstance(name, str) or not name.strip():
                continue
            weight = LAndGPosition._weight_pct(weight_raw)
            if weight is None or weight <= 0:
                continue
            raw_pairs.append((name.strip(), weight))
        logger.info("L&G: detected raw sectors: %r", raw_pairs)
        pairs: list[tuple[str, float]] = []
        for name, weight in raw_pairs:
            pairs.append((LAndGPosition._display_sector(name), weight))
        return pairs

    @staticmethod
    def _sectors_from_portfolio_html(
        html_text: str,
    ) -> list[dict[str, float | str]]:
        weights: dict[str, float] = {}
        for match in _SECTOR_DATA_RE.finditer(html_text):
            pairs = LAndGPosition._pairs_from_sector_json(match.group(1))
            if pairs:
                for name, weight in pairs:
                    weights[name] = weights.get(name, 0.0) + weight
                break
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        logger.info("L&G: fetching Sector canvas for %s", self._isin)
        try:
            ids = _resolve_shareclass(self._isin, _LANDG_FETCH_TIMEOUT_S)
            if ids is None:
                raise RuntimeError(f"L&G listing has no share class for {self._isin}")
            # Override part_id for sector canvas
            ids["part_id"] = _LANDG_SECTOR_PART_ID
            url = _part_url(ids)
            logger.info("L&G: fetching Sector canvas from %s", url)
            status, _content_type, raw = _http_get(
                url,
                _LANDG_FETCH_TIMEOUT_S,
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            if not (200 <= status < 400):
                raise RuntimeError(
                    f"L&G HTTP {status} while fetching sectors for {self._isin}"
                )
            html_text = raw.decode("utf-8", errors="replace")
            rows = LAndGPosition._sectors_from_portfolio_html(html_text)
            if rows:
                return rows
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"L&G HTTP {e.code} while fetching sectors for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"L&G connection failed while fetching sectors for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"L&G sector parse failed for {self._isin}: {e}"
            ) from e
        logger.warning("L&G: no Sector weights for %s, falling back to JustETF", self._isin)
        return super()._http_sector_dist_json()
