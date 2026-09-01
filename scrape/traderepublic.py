"""
Trade Republic positions via the ``pytr`` library (login, compact portfolio, cash).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from common import CASH_PORTFOLIO
from logger import attach_color_stderr_handler_for_module
from utils import bucket_for_isin, portfolio as global_portfolio

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

_TRADEREPUBLIC = "traderepublic"
_CASH_NAME = "Cash"
_CASH_FETCH_KEY = "__TRADEREPUBLIC_CASH__"
_PYTR_CREDENTIALS = Path.home() / ".pytr" / "credentials"


def _load_pytr_credentials() -> tuple[str | None, str | None]:
    """Read phone + pin from ``~/.pytr/credentials`` (two lines), or ``(None, None)``."""
    if not _PYTR_CREDENTIALS.is_file():
        return None, None
    try:
        lines = _PYTR_CREDENTIALS.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("traderepublic: could not read %s: %s", _PYTR_CREDENTIALS, exc)
        return None, None
    if len(lines) < 2:
        logger.warning(
            "traderepublic: %s must have phone (line 1) and pin (line 2)",
            _PYTR_CREDENTIALS,
        )
        return None, None
    phone_no = lines[0].strip()
    pin = lines[1].strip()
    if not phone_no or not pin:
        logger.warning("traderepublic: %s has empty phone or pin", _PYTR_CREDENTIALS)
        return None, None
    return phone_no, pin


@dataclass(frozen=True)
class TradeRepublicHolding:
    """One broker holding or cash row from ``pytr``."""

    isin: str | None
    name: str
    shares: float | None
    value: float
    price: float | None
    is_cash: bool = False


global global_traderepublic_holdings
global_traderepublic_holdings: dict[str, TradeRepublicHolding] = {}


def _import_pytr():
    try:
        from pytr.account import login as pytr_login
        from pytr.portfolio import Portfolio as PytrPortfolio
    except ImportError as e:
        raise ImportError(
            "pytr is required for Trade Republic scraping. "
            "Install from the local repo: pip install -e ../pytr"
        ) from e
    return pytr_login, PytrPortfolio


class TradeRepublic:
    """
    Session wrapper around ``pytr``: web login (app confirmation / authenticator),
    then compact portfolio plus cash, then websocket close.
    """

    def __init__(
        self,
        *,
        phone_no: str | None = None,
        pin: str | None = None,
        v2: bool = True,
        store_credentials: bool = False,
    ) -> None:
        if phone_no is None and pin is None:
            phone_no, pin = _load_pytr_credentials()
            if phone_no is not None:
                logger.info("traderepublic: loaded credentials from %s", _PYTR_CREDENTIALS)
        self._phone_no = phone_no
        self._pin = pin
        self._v2 = v2
        self._store_credentials = store_credentials
        self._tr: Any = None
        self._logged_in = False

    def __enter__(self) -> TradeRepublic:
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()

    def login(self) -> None:
        logger.info("traderepublic: starting pytr login (v2=%s)", self._v2)
        pytr_login, _ = _import_pytr()
        self._tr = pytr_login(
            phone_no=self._phone_no,
            pin=self._pin,
            store_credentials=self._store_credentials,
            waf_token="default",
            v2=self._v2,
        )
        self._logged_in = True
        logger.info("traderepublic: login complete")

    def logout(self) -> None:
        if not self._logged_in:
            return
        logger.info("traderepublic: closing pytr session")
        try:
            if self._tr is not None:
                asyncio.run(self._tr.close())
        except Exception as exc:
            logger.warning("traderepublic: logout error: %s", exc)
        finally:
            self._logged_in = False
            self._tr = None

    def portfolio_and_cash(self) -> tuple[list[dict[str, Any]], Any]:
        self._require_session()
        _, PytrPortfolio = _import_pytr()
        portfolio = PytrPortfolio(self._tr, include_watchlist=False)
        asyncio.run(portfolio.portfolio_loop())
        return list(portfolio.positions), portfolio.cash

    def _require_session(self) -> None:
        if not self._logged_in or self._tr is None:
            raise RuntimeError("pytr session is not logged in")


def _as_float(value: Any, *, field: str, index: int | None = None) -> float:
    try:
        return float(Decimal(str(value)))
    except (TypeError, ValueError, ArithmeticError) as e:
        where = f" item {index}" if index is not None else ""
        raise ValueError(f"pytr portfolio{where} {field} is not a number: {value!r}") from e


def parse_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ``pytr.Portfolio.positions`` into holding dicts."""
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(positions):
        if not isinstance(item, dict):
            raise ValueError(f"pytr portfolio item {i} must be an object")
        isin = str(item.get("instrumentId") or item.get("isin") or "").strip()
        if not isin:
            raise ValueError(f"pytr portfolio item {i} missing instrumentId/isin")
        if "netSize" not in item:
            raise ValueError(f"pytr portfolio item {i} missing netSize")
        if "price" not in item:
            raise ValueError(f"pytr portfolio item {i} missing price")
        if "netValue" not in item:
            raise ValueError(f"pytr portfolio item {i} missing netValue")
        shares = _as_float(item["netSize"], field="netSize", index=i)
        if shares == 0:
            logger.info("traderepublic scrape: skipping zero-size ISIN=%s", isin)
            continue
        price = _as_float(item["price"], field="price", index=i)
        value = _as_float(item["netValue"], field="netValue", index=i)
        name = str(item.get("name") or isin)
        logger.info(
            "traderepublic scrape: ISIN=%s name=%s price=%s netValue=%s netSize=%s",
            isin,
            name,
            price,
            value,
            shares,
        )
        rows.append(
            {
                "isin": isin,
                "name": name,
                "shares": shares,
                "value": value,
                "price": price,
            }
        )
    return rows


def cash_row(cash: Any) -> dict[str, Any] | None:
    """Return a Cash dict from ``pytr.Portfolio.cash``, or None if absent."""
    if not cash:
        return None
    first = cash[0] if isinstance(cash, list) else cash
    if not isinstance(first, dict):
        raise ValueError("pytr cash payload must be an object or a list of objects")
    amount = first.get("amount")
    if amount is None:
        return None
    ccy = str(first.get("currencyId") or "").upper()
    if ccy and ccy != "EUR":
        raise ValueError(f"pytr cash currencyId {ccy!r} is not EUR")
    balance = _as_float(amount, field="amount")
    logger.info(
        "traderepublic scrape: ISIN=%s name=%s price=%s netValue=%s netSize=%s",
        None,
        _CASH_NAME,
        None,
        balance,
        None,
    )
    return {
        "isin": None,
        "name": _CASH_NAME,
        "shares": None,
        "value": balance,
        "price": None,
        "is_cash": True,
    }


def holdings_to_rows(holdings: list[dict[str, Any]]) -> dict[str, TradeRepublicHolding]:
    rows: dict[str, TradeRepublicHolding] = {}
    for item in holdings:
        isin = item["isin"]
        rows[isin] = TradeRepublicHolding(
            isin=isin,
            name=item["name"],
            shares=item["shares"],
            value=item["value"],
            price=item["price"],
            is_cash=False,
        )
    return rows


def fetch_traderepublic_etfs(
    *,
    phone_no: str | None = None,
    pin: str | None = None,
    v2: bool = True,
    store_credentials: bool = False,
) -> dict[str, TradeRepublicHolding]:
    """
    Login with ``pytr``, scrape compact portfolio and cash, then close the session.
    Confirm the login in the Trade Republic app when prompted (v2 default).
    """
    rows: dict[str, TradeRepublicHolding] = {}
    session = TradeRepublic(
        phone_no=phone_no,
        pin=pin,
        v2=v2,
        store_credentials=store_credentials,
    )
    try:
        session.login()
        positions, cash = session.portfolio_and_cash()
        rows = holdings_to_rows(parse_positions(positions))
        cash_holding = cash_row(cash)
        if cash_holding is not None:
            rows[_CASH_FETCH_KEY] = TradeRepublicHolding(
                isin=None,
                name=cash_holding["name"],
                shares=None,
                value=cash_holding["value"],
                price=None,
                is_cash=True,
            )
        else:
            logger.info("traderepublic: cash scan returned no balance")
    finally:
        session.logout()
    return rows


def _is_portfolio_position_traderepublic_cash(position: dict[str, Any]) -> bool:
    pos_name = position.get("name") or position.get("Name") or ""
    pos_broker = position.get("broker") or position.get("Broker")
    return pos_name == _CASH_NAME and pos_broker == _TRADEREPUBLIC


def update_traderepublic_etfs_in_portfolio() -> None:
    global global_traderepublic_holdings
    global_traderepublic_holdings = fetch_traderepublic_etfs()
    fetched = global_traderepublic_holdings
    if not fetched:
        logger.warning(
            "update_traderepublic_etfs_in_portfolio: no Trade Republic holdings fetched; "
            "leaving portfolio unchanged",
        )
        return

    fetched_by_isin = {
        holding.isin: holding
        for holding in fetched.values()
        if not holding.is_cash and holding.isin
    }
    fetched_cash = fetched.get(_CASH_FETCH_KEY)
    to_remove: list[tuple[str, dict[str, Any]]] = []
    matched_isins: set[str] = set()
    cash_matched = False

    for bucket, positions in global_portfolio.items():
        for position in positions:
            pos_broker = position.get("broker") or position.get("Broker")
            if pos_broker != _TRADEREPUBLIC:
                continue
            if _is_portfolio_position_traderepublic_cash(position):
                if fetched_cash is None:
                    to_remove.append((bucket, position))
                    logger.info(
                        "update_traderepublic_etfs_in_portfolio: removing stale Trade Republic Cash from %r",
                        bucket,
                    )
                else:
                    position["value"] = fetched_cash.value
                    position["shares"] = None
                    position["ISIN"] = None
                    position.pop("price", None)
                    cash_matched = True
                continue
            pos_isin = position.get("ISIN") or position.get("isin")
            holding = fetched_by_isin.get(pos_isin)
            if holding is None:
                to_remove.append((bucket, position))
                logger.info(
                    "update_traderepublic_etfs_in_portfolio: removing stale Trade Republic ISIN %s from %r",
                    pos_isin,
                    bucket,
                )
                continue
            position["value"] = holding.value
            position["shares"] = holding.shares
            position["price"] = holding.price
            matched_isins.add(holding.isin)

    if fetched_cash is not None and not cash_matched:
        global_portfolio.setdefault(CASH_PORTFOLIO, []).append(
            {
                "name": _CASH_NAME,
                "ISIN": None,
                "shares": None,
                "value": fetched_cash.value,
                "broker": _TRADEREPUBLIC,
                "dmem": None,
                "dmem_other": None,
                "usavn": None,
            }
        )
        logger.info(
            "update_traderepublic_etfs_in_portfolio: added Cash to %r (value=%s)",
            CASH_PORTFOLIO,
            fetched_cash.value,
        )

    for holding in fetched_by_isin.values():
        if holding.isin in matched_isins:
            continue
        bucket = bucket_for_isin(holding.isin)
        global_portfolio.setdefault(bucket, []).append(
            {
                "name": holding.name,
                "ISIN": holding.isin,
                "shares": holding.shares,
                "value": holding.value,
                "price": holding.price,
                "broker": _TRADEREPUBLIC,
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
        logger.info(
            "update_traderepublic_etfs_in_portfolio: added missing ISIN %s to %r (value=%s)",
            holding.isin,
            bucket,
            holding.value,
        )

    for bucket, position in to_remove:
        global_portfolio[bucket].remove(position)
