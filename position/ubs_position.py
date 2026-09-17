from __future__ import annotations

import json
import logging
from typing import Any

import ccy

from logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition
from position.position import (
    _LIST_OF_DEVELOPED_MARKETS,
    _LIST_OF_EMERGING_MARKETS,
    _OTHER_MARKET_NAME,
)

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_UBS_API_BASE = "https://www.ubs.com/app/HA4/api"
_UBS_TOKEN_URL = f"{_UBS_API_BASE}/api/token-service/get-token"
_UBS_INST_ID_URL = f"{_UBS_API_BASE}/api/etf-funddetail-services/etfinstidfromisin"
_UBS_GRAPHQL_URL = f"{_UBS_API_BASE}/graphql/"
_UBS_LOCALE = "en_CH_RETL"
_UBS_SEGMENT_KEY = "etf.emwh"
_UBS_EXISTS_TIMEOUT_S = 10
_UBS_FETCH_TIMEOUT_S = 30

_CONSTITUENTS_QUERY = """
query GetConstituentsExcelv2($instId: String!, $sgmtKey: String!, $locale: String!) {
  getConstituentsExcel(instId: $instId, sgmtKey: $sgmtKey, locale: $locale) {
    etfFundHoldingsLargestConstituents {
      row {
        type
        cell { id data }
      }
    }
  }
}
"""

# Non-country ISIN prefixes (ISO 6166 / ANNA): Euroclear/Clearstream, CINS
# substitutes, and reserved/test codes.
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

_MARKET_NAMES: tuple[str, ...] = tuple(
    _LIST_OF_DEVELOPED_MARKETS + _LIST_OF_EMERGING_MARKETS
)
_MARKET_BY_LOWER: dict[str, str] = {name.casefold(): name for name in _MARKET_NAMES}

_CCY_NAME_ALIASES: dict[str, str] = {
    "macao": "Macau",
    "eurozone": "European Union",
}

_ISIN_PREFIX_TO_MARKET: dict[str, str] = {
    "EU": "European Union",
    "UK": "United Kingdom",
}

_CURRENCY_ALIASES: dict[str, str] = {
    "CNH": "CNY",
}

_UBS_PRODUCT_PAGE = (
    "https://www.ubs.com/ch/en/assetmanagement/funds/etf/{isin}-pd001.html"
)
_UBS_PRODUCT_EXISTS: dict[str, bool] = {}


class _Ha4HttpError(RuntimeError):
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        super().__init__(f"UBS HTTP {status} for {url}")


def _ubs_product_page_url(isin: str) -> str:
    return _UBS_PRODUCT_PAGE.format(isin=isin.lower())


def _ha4_headers(*, token: str | None = None, referer: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "locale": _UBS_LOCALE,
        "Origin": "https://www.ubs.com",
        "Referer": referer
        or "https://www.ubs.com/ch/en/assetmanagement/funds/etf/",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _new_ha4_session():
    try:
        from curl_cffi import requests
    except ImportError as e:
        raise RuntimeError(
            "curl_cffi is required to fetch UBS constituents"
        ) from e
    return requests.Session(impersonate="chrome")


def _ha4_request(
    session,
    method: str,
    url: str,
    timeout_s: float,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
):
    response = session.request(
        method,
        url,
        headers=headers,
        json=json_body,
        timeout=timeout_s,
    )
    if response.status_code >= 400:
        raise _Ha4HttpError(response.status_code, url)
    return response


def _http_token(
    timeout_s: float,
    *,
    session,
    referer: str | None = None,
) -> str:
    response = _ha4_request(
        session,
        "GET",
        _UBS_TOKEN_URL,
        timeout_s,
        headers=_ha4_headers(referer=referer),
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("UBS token JSON is not an object")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("UBS token JSON has no token")
    return token


def _http_inst_id(
    isin: str,
    token: str,
    timeout_s: float,
    *,
    session,
    referer: str | None = None,
) -> str | None:
    response = _ha4_request(
        session,
        "POST",
        _UBS_INST_ID_URL,
        timeout_s,
        headers={
            **_ha4_headers(token=token, referer=referer),
            "Content-Type": "application/json",
        },
        json_body={
            "isin": isin,
            "locale": _UBS_LOCALE,
            "sgmtKey": _UBS_SEGMENT_KEY,
        },
    )
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    inst_id = payload.get("instId")
    if inst_id is None:
        return None
    text = str(inst_id).strip()
    return text or None


def _seed_ubs_product_page(session, isin: str, timeout_s: float) -> None:
    url = _ubs_product_page_url(isin)
    try:
        session.get(url, timeout=timeout_s)
    except Exception as e:
        logger.info("UBS product page seed for %s failed (%s)", isin, e)


def ubs_product_url_exists(isin: str) -> bool:
    """True when UBS HA4 can resolve ``isin`` to an ETF instrument id."""
    cached = _UBS_PRODUCT_EXISTS.get(isin)
    if cached is not None:
        return cached
    if not isin:
        _UBS_PRODUCT_EXISTS[isin] = False
        return False
    exists = False
    try:
        session = _new_ha4_session()
        _seed_ubs_product_page(session, isin, _UBS_EXISTS_TIMEOUT_S)
        token = _http_token(_UBS_EXISTS_TIMEOUT_S, session=session)
        inst_id = _http_inst_id(isin, token, _UBS_EXISTS_TIMEOUT_S, session=session)
        exists = bool(inst_id)
    except _Ha4HttpError as e:
        exists = False
        logger.info("UBS HA4 lookup for %s returned HTTP %s", isin, e.status)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, OSError) as e:
        exists = False
        logger.warning("UBS HA4 lookup failed for %s (%s)", isin, e)
    _UBS_PRODUCT_EXISTS[isin] = exists
    return exists


class UBSPosition(JustETFPosition):
    """JustETF quotes with country weights from UBS HA4 constituents JSON."""

    ISINS: frozenset[str] = frozenset(
        {
            "IE00BD4TXV59",
            "IE00BKSCBX74",
        }
    )

    @staticmethod
    def _ccy_name_candidates(record: object) -> list[str]:
        names: list[str] = []
        for attr in ("common_name", "name", "official_name"):
            value = getattr(record, attr, None)
            if isinstance(value, str) and value and value not in names:
                names.append(value)
        return names

    @staticmethod
    def _name_for_market_lists(record: object) -> str:
        candidates = UBSPosition._ccy_name_candidates(record)
        for candidate in candidates:
            listed = _MARKET_BY_LOWER.get(candidate.casefold())
            if listed:
                return listed
            aliased = _CCY_NAME_ALIASES.get(candidate.casefold())
            if aliased is not None:
                return aliased
        return candidates[0] if candidates else _OTHER_MARKET_NAME

    @staticmethod
    def _name_from_alpha2(code: str) -> str | None:
        if code == "EU":
            return "European Union"
        try:
            record = ccy.country(code)
        except (KeyError, ValueError, TypeError):
            return None
        return UBSPosition._name_for_market_lists(record)

    @staticmethod
    def _parse_weight_pct(raw: str) -> float | None:
        stripped = raw.strip().replace(",", "").replace("\u00a0", "").rstrip("%")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    @staticmethod
    def _country_from_isin(raw_isin: str) -> str | None:
        """Map ISIN prefix to economic-home country; special/unknown -> Other."""
        code = raw_isin.strip()[:2].upper()
        if len(code) != 2 or not code.isalpha():
            return None
        if code in _ISIN_SPECIAL_PREFIXES:
            return _OTHER_MARKET_NAME
        aliased = _ISIN_PREFIX_TO_MARKET.get(code)
        if aliased is not None:
            return aliased
        name = UBSPosition._name_from_alpha2(code)
        return name if name else _OTHER_MARKET_NAME

    @staticmethod
    def _country_from_currency(raw_currency: str) -> str | None:
        """Map listing currency to a market name; unknown currency -> None."""
        code = raw_currency.strip().upper()
        code = _CURRENCY_ALIASES.get(code, code)
        if not code:
            return None
        try:
            currency = ccy.currency(code)
        except (KeyError, ValueError, TypeError):
            return None
        alpha2 = getattr(currency, "default_country", None)
        if not isinstance(alpha2, str) or not alpha2:
            return None
        return UBSPosition._name_from_alpha2(alpha2)

    @staticmethod
    def _country_from_holding(raw_isin: str, raw_currency: str) -> str | None:
        """Prefer listing currency; else ISIN prefix."""
        name = UBSPosition._country_from_currency(raw_currency)
        if name:
            return name
        return UBSPosition._country_from_isin(raw_isin)

    @staticmethod
    def _cell_map(row: object) -> dict[str, str]:
        if not isinstance(row, dict):
            return {}
        cells = row.get("cell")
        if not isinstance(cells, list):
            return {}
        mapped: dict[str, str] = {}
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            cell_id = cell.get("id")
            data = cell.get("data")
            if isinstance(cell_id, str) and cell_id:
                mapped[cell_id] = data if isinstance(data, str) else ""
        return mapped

    @staticmethod
    def _records_from_constituents_payload(
        payload: dict[str, Any],
    ) -> list[dict[str, str]]:
        data = payload.get("data")
        root = data if isinstance(data, dict) else payload
        excel = root.get("getConstituentsExcel")
        if not isinstance(excel, dict):
            return []
        block = excel.get("etfFundHoldingsLargestConstituents")
        if not isinstance(block, dict):
            return []
        rows = block.get("row")
        if not isinstance(rows, list):
            return []
        records: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "data":
                continue
            cells = UBSPosition._cell_map(row)
            records.append(
                {
                    "isin": cells.get("P_ISIN", "").strip(),
                    "currency": cells.get("Currency", "").strip(),
                    "weight": cells.get("Weight", "").strip(),
                }
            )
        return records

    @staticmethod
    def _countries_from_holding_records(
        records: list[dict[str, str]],
    ) -> list[dict[str, float | str]]:
        weights: dict[str, float] = {}
        for record in records:
            name = UBSPosition._country_from_holding(
                record.get("isin", ""),
                record.get("currency", ""),
            )
            if not name:
                continue
            weight = UBSPosition._parse_weight_pct(record.get("weight", ""))
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @staticmethod
    def _countries_from_constituents_payload(
        payload: dict[str, Any],
    ) -> list[dict[str, float | str]]:
        records = UBSPosition._records_from_constituents_payload(payload)
        return UBSPosition._countries_from_holding_records(records)

    def _http_constituents_payload(
        self,
        inst_id: str,
        token: str,
        *,
        session,
        referer: str | None = None,
    ) -> dict[str, Any]:
        logger.info(
            "UBS: fetching constituents JSON from %s for instId %s",
            _UBS_GRAPHQL_URL,
            inst_id,
        )
        response = _ha4_request(
            session,
            "POST",
            _UBS_GRAPHQL_URL,
            _UBS_FETCH_TIMEOUT_S,
            headers={
                **_ha4_headers(token=token, referer=referer),
                "Content-Type": "application/json",
            },
            json_body={
                "query": _CONSTITUENTS_QUERY,
                "variables": {
                    "instId": inst_id,
                    "sgmtKey": _UBS_SEGMENT_KEY,
                    "locale": _UBS_LOCALE,
                },
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("UBS constituents JSON is not an object")
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"UBS GraphQL errors for {self._isin}: {errors}")
        return payload

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        referer = _ubs_product_page_url(self._isin)
        session = _new_ha4_session()
        _seed_ubs_product_page(session, self._isin, _UBS_FETCH_TIMEOUT_S)
        try:
            token = _http_token(
                _UBS_FETCH_TIMEOUT_S, session=session, referer=referer
            )
            inst_id = _http_inst_id(
                self._isin,
                token,
                _UBS_FETCH_TIMEOUT_S,
                session=session,
                referer=referer,
            )
            if not inst_id:
                raise RuntimeError(f"UBS instId is unknown for {self._isin}")
            payload = self._http_constituents_payload(
                inst_id, token, session=session, referer=referer
            )
            rows = UBSPosition._countries_from_constituents_payload(payload)
        except _Ha4HttpError as e:
            raise RuntimeError(
                f"UBS HTTP {e.status} while fetching countries for {self._isin}"
            ) from e
        except OSError as e:
            # Also covers curl_cffi connection/timeout errors (OSError subclasses).
            raise RuntimeError(
                f"UBS connection failed while fetching countries for {self._isin}: {e}"
            ) from e
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError, KeyError) as e:
            raise RuntimeError(
                f"UBS constituents parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning(
                "UBS: no country weights in constituents for %s", self._isin
            )
        return rows
