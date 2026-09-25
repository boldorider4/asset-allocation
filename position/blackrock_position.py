# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import csv
import io
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from cli.logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition
from position.position import fold_unknown_sector_label

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_ISHARES_HOLDINGS_URL = (
    "https://www.ishares.com/ch/individual/en/products/{product_id}"
    "/fund/1495092304805.ajax?fileType=csv"
)
_ISHARES_EXISTS_TIMEOUT_S = 10
_ISHARES_FETCH_TIMEOUT_S = 30


# iShares Location labels -> names used in Position DMEM/USAVN lists.
_ISHARES_COUNTRY_ALIASES: dict[str, str] = {
    "Korea (South)": "South Korea",
    "Korea, Republic of": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea": "South Korea",
    "Russian Federation": "Russia",
}

_ISHARES_PRODUCT_EXISTS: dict[str, bool] = {}


def _ishares_holdings_url(product_id: str | None) -> str | None:
    if not product_id:
        return None
    return _ISHARES_HOLDINGS_URL.format(product_id=product_id)


def _content_type_is_csv(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "csv" in content_type.lower()


def ishares_product_url_exists(isin: str, product_id: str | None = None) -> bool:
    """True when the iShares CH holdings CSV for ``isin`` is reachable.

    ``product_id`` comes from the ISIN registry; ``None`` (unknown ISIN)
    means unreachable without any network traffic.
    """
    cached = _ISHARES_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    url = _ishares_holdings_url(product_id)
    if not url:
        _ISHARES_PRODUCT_EXISTS[isin] = False
        return False
    req = urllib.request.Request(
        url,
        headers=JustETFPosition._HEADERS,
        method="GET",
    )
    exists = False
    try:
        with urllib.request.urlopen(req, timeout=_ISHARES_EXISTS_TIMEOUT_S) as resp:
            status_ok = 200 <= getattr(resp, "status", 200) < 400
            content_type = resp.headers.get("Content-Type") if resp.headers else None
            exists = status_ok and _content_type_is_csv(content_type)
    except urllib.error.HTTPError as e:
        logger.info("iShares holdings URL %s returned HTTP %s", url, e.code)
    except urllib.error.URLError as e:
        logger.warning("iShares holdings URL check failed for %s (%s)", isin, e)
    except OSError as e:
        # Read timeouts, resets, DNS/SSL failures: bail to cached data.
        logger.warning("iShares holdings URL check connection failed for %s (%s)", isin, e)
    _ISHARES_PRODUCT_EXISTS[isin] = exists
    return exists


class BlackRockPosition(JustETFPosition):
    """JustETF quotes with country and sector weights from the iShares holdings CSV."""

    _ISHARES_SECTOR_CANONICAL: dict[str, str] = {
        "Information Technology": "Technology",
        "Health Care": "Healthcare",
        "Consumer Discretionary": "Consumer",
        "Consumer Staples": "Consumer",
        "Financials": "Finance",
        "Communication Services": "Telecommunication",
        "Communication": "Telecommunication",
        "Industrials": "Industrials",
        "Materials": "Materials",
        "Real Estate": "Real Estate",
        "Energy": "Commodities",
        "Utilities": "Utilities",
        "Sovereign": "Government",
        "Government Related": "Government",
        "Cash and/or Derivatives": "Other",
        "Cash & Derivatives": "Other",
    }

    def __init__(
        self, isin: str,
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
        self._holdings_csv: str | None = None
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
    def _parse_weight_pct(raw: str) -> float | None:
        stripped = (
            raw.strip()
            .replace(",", "")
            .replace("\u2019", "")
            .replace("'", "")
            .rstrip("%")
        )
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    @staticmethod
    def _is_disclaimer_row(record: list[str]) -> bool:
        if not record:
            return True
        first = record[0].strip()
        if not first:
            return True
        if len(first) > 100:
            return True
        lowered = first.lower()
        return any(
            marker in lowered
            for marker in (
                "©",
                "http://",
                "https://",
                "www.",
                "the content",
                "this information",
            )
        )

    @staticmethod
    def _countries_from_holdings_csv(text: str) -> list[dict[str, float | str]]:
        """Sum iShares holdings rows by Location into JustETF-shaped rows."""
        reader = csv.reader(io.StringIO(text))
        header: list[str] | None = None
        for record in reader:
            if "Location" in record and any(col.startswith("Weight") for col in record):
                header = record
                break
        if header is None:
            return []
        try:
            loc_i = header.index("Location")
            weight_i = next(
                i for i, col in enumerate(header) if col.startswith("Weight")
            )
        except (ValueError, StopIteration):
            return []
        weights: dict[str, float] = {}
        for record in reader:
            if BlackRockPosition._is_disclaimer_row(record):
                break
            if len(record) <= max(loc_i, weight_i):
                continue
            raw_name = record[loc_i].strip()
            if not raw_name:
                continue
            name = _ISHARES_COUNTRY_ALIASES.get(raw_name, raw_name)
            weight = BlackRockPosition._parse_weight_pct(record[weight_i])
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @staticmethod
    def _sectors_from_holdings_csv(text: str) -> list[dict[str, float | str]]:
        """Sum iShares holdings rows by Sector into JustETF-shaped rows."""
        reader = csv.reader(io.StringIO(text))
        header: list[str] | None = None
        for record in reader:
            if "Sector" in record and any(col.startswith("Weight") for col in record):
                header = record
                break
        if header is None:
            return []
        try:
            sector_i = header.index("Sector")
            weight_i = next(
                i for i, col in enumerate(header) if col.startswith("Weight")
            )
        except (ValueError, StopIteration):
            return []
        raw_weights: dict[str, float] = {}
        for record in reader:
            if BlackRockPosition._is_disclaimer_row(record):
                break
            if len(record) <= max(sector_i, weight_i):
                continue
            raw_sector = record[sector_i].strip()
            if not raw_sector:
                continue
            weight = BlackRockPosition._parse_weight_pct(record[weight_i])
            if weight is None or weight <= 0:
                continue
            raw_weights[raw_sector] = raw_weights.get(raw_sector, 0.0) + weight
        logger.info("BlackRock: detected raw sectors: %r", raw_weights)
        weights: dict[str, float] = {}
        for raw_sector, weight in raw_weights.items():
            canonical = BlackRockPosition._ISHARES_SECTOR_CANONICAL.get(raw_sector, raw_sector)
            canonical = fold_unknown_sector_label(canonical)
            weights[canonical] = weights.get(canonical, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        url = _ishares_holdings_url(
            self._ctx.isin_registry.get_product_ref_for_isin(self._isin)
        )
        if not url:
            raise RuntimeError(
                f"iShares product id is unknown for {self._isin}"
            )
        req = urllib.request.Request(
            url,
            headers=self._HEADERS,
            method="GET",
        )
        logger.info("iShares: fetching holdings CSV from %s", url)
        try:
            with urllib.request.urlopen(req, timeout=_ISHARES_FETCH_TIMEOUT_S) as resp:
                content_type = resp.headers.get("Content-Type") if resp.headers else None
                if not _content_type_is_csv(content_type):
                    raise RuntimeError(
                        f"iShares holdings for {self._isin} is not CSV ({content_type})"
                    )
                body = resp.read().decode("utf-8-sig", errors="replace")
            self._holdings_csv = body
            rows = self._countries_from_holdings_csv(body)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"iShares HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"iShares connection failed while fetching countries for {self._isin}: {e}"
            ) from e
        except (csv.Error, TypeError, ValueError, UnicodeError) as e:
            raise RuntimeError(
                f"iShares holdings parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning("iShares: no country weights in holdings for %s", self._isin)
        return rows

    def _http_sector_dist_json(self) -> list[dict[str, float | str]]:
        if self._holdings_csv is not None:
            logger.info("BlackRock: using cached holdings CSV for sectors (saved a fucking network call)")
            return self._sectors_from_holdings_csv(self._holdings_csv)
        logger.warning("BlackRock: no cached CSV, falling back to JustETF sector scrape")
        return super()._http_sector_dist_json()
