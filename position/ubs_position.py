from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

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

_UBS_API_BASE = "https://www.ubs.com/app/HA4/api"
_UBS_TOKEN_URL = f"{_UBS_API_BASE}/api/token-service/get-token"
_UBS_INST_ID_URL = f"{_UBS_API_BASE}/api/etf-funddetail-services/etfinstidfromisin"
_UBS_CONSTITUENTS_URL = (
    f"{_UBS_API_BASE}/api/etf-funddetail-services/{{inst_id}}"
    "/download-constituents-to-excel"
)
_UBS_LOCALE = "en_CH_RETL"
_UBS_SEGMENT_KEY = "etf.emwh"
_UBS_EXISTS_TIMEOUT_S = 10
_UBS_FETCH_TIMEOUT_S = 30

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_COL_REF_RE = re.compile(r"^([A-Z]+)")

# Non-country ISIN prefixes (ISO 6166 / ANNA): Euroclear/Clearstream, CINS
# substitutes, and reserved/test codes. Withdrawn ISO countries are historic.
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

# pycountry spellings that are the same market as a DMEM/USAVN list entry.
_PYCOUNTRY_NAME_ALIASES: dict[str, str] = {
    "macao": "Macau",
}

# ISIN prefixes that are not ISO 3166-1 alpha-2 but still appear on securities.
_ISIN_PREFIX_TO_MARKET: dict[str, str] = {
    "EU": "European Union",
    "UK": "United Kingdom",
}

_UBS_PRODUCT_EXISTS: dict[str, bool] = {}


def _ubs_api_headers(*, token: str | None = None) -> dict[str, str]:
    headers = {
        **JustETFPosition._HEADERS,
        "Accept": "*/*",
        "locale": _UBS_LOCALE,
        "Origin": "https://www.ubs.com",
        "Referer": "https://www.ubs.com/ch/en/assetmanagement/funds/etf/",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_inst_id(isin: str, token: str, timeout_s: float) -> str | None:
    body = json.dumps(
        {
            "isin": isin,
            "locale": _UBS_LOCALE,
            "sgmtKey": _UBS_SEGMENT_KEY,
        }
    ).encode()
    req = urllib.request.Request(
        _UBS_INST_ID_URL,
        data=body,
        headers={
            **_ubs_api_headers(token=token),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        return None
    inst_id = payload.get("instId")
    if inst_id is None:
        return None
    text = str(inst_id).strip()
    return text or None


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
        token = UBSPosition._http_token(_UBS_EXISTS_TIMEOUT_S)
        inst_id = _http_inst_id(isin, token, _UBS_EXISTS_TIMEOUT_S)
        exists = bool(inst_id)
    except urllib.error.HTTPError as e:
        exists = False
        logger.info("UBS HA4 lookup for %s returned HTTP %s", isin, e.code)
    except urllib.error.URLError as e:
        exists = False
        logger.warning("UBS HA4 lookup failed for %s (%s)", isin, e)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as e:
        exists = False
        logger.warning("UBS HA4 lookup parse failed for %s (%s)", isin, e)
    _UBS_PRODUCT_EXISTS[isin] = exists
    return exists


class UBSPosition(JustETFPosition):
    """JustETF quotes with country weights from the UBS ETF constituents workbook."""

    ISINS: frozenset[str] = frozenset(
        {
            "IE00BD4TXV59",
        }
    )


def _content_type_is_xlsx(content_type: str | None) -> bool:
    if not content_type:
        return False
    lowered = content_type.lower()
    return any(
        marker in lowered
        for marker in (
            "spreadsheet",
            "excel",
            "officedocument",
            "octet-stream",
            "zip",
        )
    )

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
        candidates = UBSPosition._pycountry_name_candidates(record)
        for candidate in candidates:
            listed = _MARKET_BY_LOWER.get(candidate.casefold())
            if listed:
                return listed
            aliased = _PYCOUNTRY_NAME_ALIASES.get(candidate.casefold())
            if aliased is not None:
                return aliased
        return candidates[0] if candidates else _OTHER_MARKET_NAME

    @staticmethod
    def _country_record_for_prefix(code: str) -> object | None:
        current = pycountry.countries.get(alpha_2=code)
        if current is not None:
            return current
        return pycountry.historic_countries.get(alpha_2=code)

    @staticmethod
    def _col_index(cell_ref: str) -> int:
        match = _COL_REF_RE.match(cell_ref.upper())
        if not match:
            return 0
        letters = match.group(1)
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - 64)
        return index - 1

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
    def _is_disclaimer_row(record: list[str]) -> bool:
        if not record:
            return True
        first = next((cell.strip() for cell in record if cell.strip()), "")
        if not first:
            return True
        if len(first) > 100:
            return True
        lowered = first.lower()
        return any(
            marker in lowered
            for marker in (
                "source:",
                "for marketing",
                "©",
                "http://",
                "https://",
                "www.",
            )
        )

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
        record = UBSPosition._country_record_for_prefix(code)
        if record is None:
            return _OTHER_MARKET_NAME
        return UBSPosition._name_for_market_lists(record)

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings: list[str] = []
        for item in root.findall("m:si", _XLSX_NS):
            strings.append(
                "".join(node.text or "" for node in item.findall(".//m:t", _XLSX_NS))
            )
        return strings

    @staticmethod
    def _cell_text(cell: ET.Element, shared: list[str]) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(
                node.text or "" for node in cell.findall(".//m:t", _XLSX_NS)
            )
        value = cell.find("m:v", _XLSX_NS)
        if value is None or value.text is None:
            return ""
        if cell_type == "s":
            try:
                return shared[int(value.text)]
            except (ValueError, IndexError):
                return ""
        return value.text

    @staticmethod
    def _sheet_rows(archive: zipfile.ZipFile) -> list[list[str]]:
        shared = UBSPosition._shared_strings(archive)
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall("m:sheetData/m:row", _XLSX_NS):
            values: dict[int, str] = {}
            max_i = -1
            for cell in row.findall("m:c", _XLSX_NS):
                index = UBSPosition._col_index(cell.attrib.get("r", "A"))
                values[index] = UBSPosition._cell_text(cell, shared)
                if index > max_i:
                    max_i = index
            record = [values.get(i, "") for i in range(max_i + 1)] if max_i >= 0 else []
            rows.append(record)
        return rows

    @staticmethod
    def _countries_from_holdings_xlsx(data: bytes) -> list[dict[str, float | str]]:
        """Sum constituent rows by ISIN country prefix into JustETF-shaped rows."""
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                rows = UBSPosition._sheet_rows(archive)
        except zipfile.BadZipFile:
            return []
        header: list[str] | None = None
        header_i = -1
        for index, record in enumerate(rows):
            if "ISIN" in record and any(col.startswith("Weight") for col in record):
                header = record
                header_i = index
                break
        if header is None:
            return []
        try:
            isin_i = header.index("ISIN")
            weight_i = next(
                i for i, col in enumerate(header) if col.startswith("Weight")
            )
        except (ValueError, StopIteration):
            return []
        weights: dict[str, float] = {}
        for record in rows[header_i + 1 :]:
            if UBSPosition._is_disclaimer_row(record):
                break
            if len(record) <= max(isin_i, weight_i):
                continue
            raw_isin = record[isin_i].strip()
            name = UBSPosition._country_from_isin(raw_isin)
            if not name:
                continue
            weight = UBSPosition._parse_weight_pct(record[weight_i])
            if weight is None or weight <= 0:
                continue
            weights[name] = weights.get(name, 0.0) + weight
        return [
            {"name": name, "weight_pct": weight}
            for name, weight in sorted(weights.items(), key=lambda item: -item[1])
        ]

    @staticmethod
    def _http_token(timeout_s: float) -> str:
        req = urllib.request.Request(
            _UBS_TOKEN_URL,
            headers=_ubs_api_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise RuntimeError("UBS token JSON is not an object")
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("UBS token JSON has no token")
        return token

    def _http_constituents_xlsx(self, inst_id: str, token: str) -> bytes:
        query = urllib.parse.urlencode(
            {"locale": _UBS_LOCALE, "sgmtKey": _UBS_SEGMENT_KEY}
        )
        url = f"{_UBS_CONSTITUENTS_URL.format(inst_id=inst_id)}?{query}"
        req = urllib.request.Request(
            url,
            headers=_ubs_api_headers(token=token),
            method="GET",
        )
        logger.info("UBS: fetching constituents workbook from %s", url)
        with urllib.request.urlopen(req, timeout=_UBS_FETCH_TIMEOUT_S) as resp:
            content_type = resp.headers.get("Content-Type") if resp.headers else None
            body = resp.read()
        if body[:2] != b"PK" and not _content_type_is_xlsx(content_type):
            raise RuntimeError(
                f"UBS constituents for {self._isin} is not XLSX ({content_type})"
            )
        return body

    def _http_country_dist_json(self) -> list[dict[str, float | str]]:
        try:
            token = self._http_token(_UBS_FETCH_TIMEOUT_S)
            inst_id = _http_inst_id(self._isin, token, _UBS_FETCH_TIMEOUT_S)
            if not inst_id:
                raise RuntimeError(f"UBS instId is unknown for {self._isin}")
            body = self._http_constituents_xlsx(inst_id, token)
            rows = UBSPosition._countries_from_holdings_xlsx(body)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"UBS HTTP {e.code} while fetching countries for {self._isin}"
            ) from e
        except (
            json.JSONDecodeError,
            zipfile.BadZipFile,
            ET.ParseError,
            TypeError,
            ValueError,
            UnicodeError,
            KeyError,
        ) as e:
            raise RuntimeError(
                f"UBS constituents parse failed for {self._isin}: {e}"
            ) from e
        if not rows:
            logger.warning(
                "UBS: no country weights in constituents for %s", self._isin
            )
        return rows
